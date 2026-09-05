"""
Worker 生命周期管理工具模块。

该模块实现了 Coordinator 派发和管理后台 Worker 的核心工具集，是
"发号施令→后台执行→结果回传"这一完整闭环的载体。
主要功能包括：
- 提供 `spawn_worker` 工具：根据技能黄页创建全新的 Worker 并异步启动沙箱循环。
- 提供 `send_message` 工具：向仍在运行的 Worker 追加指令；已结束的 Worker 不会被重新启动。
- 提供 `_execute_sandbox_loop` 内部沙箱引擎：负责 Worker 的 ReAct 循环
  （模型思考 → 工具调用 → 结果回写），包含超时控制、遥测统计、插队消息处理。
- 提供 `_background_task_wrapper` 壳函数：包裹沙箱循环并在结束时将结果通知
  投递到全局消息队列，唤醒 Coordinator。

Classes:
    SpawnWorkerInput
        `spawn_worker` 工具的 Pydantic 入参模型。字段：
        - agent_name: 技能包名或基础工具名，必须在黄页中存在。
        - directive: 详尽的自然语言操作指令。
        - model: 可选的模型覆盖，留空走默认解析链。

    SendMessageInput
        `send_message` 工具的 Pydantic 入参模型。字段：
        - to_agent_id: 目标 Worker 的 Task-ID。
        - message: 追加的指令或纠正要求。

Functions:
    _execute_sandbox_loop(worker_id, session_id, messages, allowed_tools_objects, model_name) -> str
        异步沙箱执行循环。最多 6 轮 ReAct 迭代，单次最长 60 秒。
        每轮先清空 pending_messages 处理 Coordinator 插队指令，再调用
        LLM 推理并执行工具。返回 JSON 通知字符串，
        内含遥测数据（总 token 数、工具调用次数、耗时毫秒）。

    _background_task_wrapper(worker_id, session_id, initial_messages, allowed_tools, model_name) -> None
        沙箱外壳协程。捕获 `_execute_sandbox_loop` 的所有异常并兜底，
        最终将结果通知通过 `global_message_queue.enqueue` 投递到主消息队列。

    spawn_worker(agent_name, directive, config, model) -> str
        LangChain `@tool`。根据 agent_name 查询技能黄页获取 SOP Prompt
        与工具白名单，解析模型（env → tool param → yaml defaults → inherit），
        组装初始消息（System + Human），写入磁盘持久化，
        通过 `asyncio.create_task` 启动后台沙箱，瞬间返回 "started" 通知。

    send_message(to_agent_id, message, config) -> str
        LangChain `@tool`。向仍在运行的 Worker 追加明确指令；若目标已结束，
        返回失败通知并由 Coordinator 综合已有结果或创建新的 Worker。

Dependencies:
    - `core.skill_loader.skill_loader`: 查询技能名是否合法、获取 SOP Prompt 与工具白名单。
    - `core.tool_registry.physical_tool_manager`: 按白名单提取 Worker 可用的实体工具。
    - `core.task_manager.global_task_manager`: 注册任务 future、查询运行中任务、投递插队消息。
    - `core.message_queue.global_message_queue`: 沙箱结束后向 Coordinator 投递结果通知。
    - `server.agent.llm_factory`: 创建 Worker LLM 实例（支持动态模型解析）。
    - `server.agent.node.session_storage`: 磁盘持久化（元数据 + JSONL 对话记录）。
    - `configs.settings.settings`: 模型名解析（_resolve_model_name）。

Side effects:
    - `spawn_worker` 会在 `.data/sessions/<session_id>/subagents/<worker_id>/`
      下创建 metadata.json 和 transcript.jsonl 文件。
    - 通过 `asyncio.create_task` 创建的后台任务不阻塞调用方，Coordinator 立即返回。
    - 沙箱超时（60s）或轮次耗尽（6 轮）均不会导致进程崩溃，错误通过通知 JSON 回传。
"""
import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from core.task_manager import global_task_manager
from core.worker_events import WorkerCompletedEvent, global_worker_event_bus
from core.worker_lifecycle import (
    WorkerResourceBudget,
    WorkerRetryPolicy,
    classify_worker_error,
)
from core.worker_protocol import WorkerCommand, WorkerWaitMode
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from core.skill_loader import ResolvedWorkerProfile, skill_loader
from server.agent.llm_factory import (
    get_worker_llm,
    get_tool_llm,
    resolve_worker_model_name,
    worker_model_uses_native_search,
)
from core.tool_registry import physical_tool_manager
from core.tool_runtime import ToolGrantSnapshot, ToolSet
from core.session_context import SessionContext
from core.agent_runtime import (
    AgentStopReason,
    exhaustion_fallback,
    exhaustion_prompt,
    usage_total,
)
from core.observability.context import current_telemetry_context
from core.observability.models import SpanKind, SpanStatus
from core.observability.runtime import global_telemetry
from core.prompt_runtime import global_prompt_runtime
from server.agent.compression.context_manager import global_context_manager
from utils.tokens import build_context_budget
from server.agent.node.session_storage import (
    write_agent_metadata,
    record_sidechain_transcript,
)
from utils.logger import get_logger
from utils.uuid import create_agent_id
from schemas.models import (
    WorkerErrorSpec,
    WorkerExecutionResultSpec,
    WorkerNotificationSpec,
    WorkerTimingSpec,
    WorkerUsageSpec,
)

logger = get_logger("shiliu.tools.worker")

NON_PERSISTED_TOOL_RESULT_PLACEHOLDER = (
    "[tool result omitted from persistent transcript by tool policy]"
)
_TOOL_RUNTIME_OVERHEAD_S = 30.0
_JOIN_COMPLETION_GRACE_S = 5.0


def _aligned_worker_timeouts(
    toolset: ToolSet,
    *,
    max_duration_s: float,
    wait_timeout_s: float,
    join: bool,
) -> tuple[float, float]:
    """Keep a Worker alive long enough for its slowest granted tool.

    Tool timeouts apply per attempt.  The Worker also needs time for the model
    turn before and after the tool call, while a joining Coordinator needs a
    small completion-delivery grace period.
    """

    worst_tool_runtime_s = 0.0
    for descriptor in toolset.descriptors:
        retry = descriptor.retry
        retry_delays_s = sum(
            min(
                retry.max_delay_s,
                retry.base_delay_s
                * (2 ** max(0, failed_attempt - 1))
                * (1 + retry.jitter_ratio),
            )
            for failed_attempt in range(1, retry.max_attempts)
        )
        worst_tool_runtime_s = max(
            worst_tool_runtime_s,
            descriptor.timeout_s * retry.max_attempts + retry_delays_s,
        )

    required_duration_s = worst_tool_runtime_s + _TOOL_RUNTIME_OVERHEAD_S
    if required_duration_s <= max_duration_s:
        return max_duration_s, wait_timeout_s
    if required_duration_s > 1_800:
        raise ValueError("granted tool timeouts exceed the Worker duration limit")

    aligned_wait_s = wait_timeout_s
    if join:
        aligned_wait_s = max(
            wait_timeout_s, required_duration_s + _JOIN_COMPLETION_GRACE_S
        )
        if aligned_wait_s > 600:
            raise ValueError("granted tool timeouts exceed the Worker join-wait limit")
    return required_duration_s, aligned_wait_s


def _resolve_profile_worker_model(
    profile: ResolvedWorkerProfile,
    *,
    requested_model: str = "",
    model_profile: str | None = None,
) -> str:
    explicit = requested_model.strip()
    if explicit == "inherit":
        explicit = ""
    if profile.model and explicit and explicit != profile.model:
        raise ValueError(
            f"Worker Profile {profile.name!r} pins model {profile.model!r}; "
            f"override {explicit!r} is not allowed"
        )
    resolved = resolve_worker_model_name(
        profile.name,
        profile.model or explicit or None,
        model_profile,
    )
    if profile.model and resolved != profile.model:
        raise ValueError(
            f"Worker Profile {profile.name!r} requires model {profile.model!r}, "
            f"but the global Worker override selected {resolved!r}"
        )
    native_search = worker_model_uses_native_search(resolved)
    if profile.requires_native_search and not native_search:
        raise ValueError(
            f"Worker Profile {profile.name!r} requires a native-search model preset"
        )
    if native_search and not profile.requires_native_search:
        raise ValueError(
            f"Worker Profile {profile.name!r} is not authorized to use native-search "
            f"preset {resolved!r}"
        )
    return resolved


def _build_profile_execution_policies(
    profile: ResolvedWorkerProfile,
    *,
    runtime_settings: dict,
    max_turns: int,
    max_duration_s: float,
    max_tokens: int,
    max_tool_calls: int,
    max_attempts: int,
) -> tuple[WorkerResourceBudget, WorkerRetryPolicy]:
    one_shot = profile.execution_mode == "one_shot"
    budget = WorkerResourceBudget(
        max_turns=1 if one_shot else max_turns,
        max_duration_s=max_duration_s,
        max_tokens=max_tokens,
        max_tool_calls=0 if one_shot else max_tool_calls,
        max_injections=(
            0 if one_shot else int(runtime_settings.get("max_injections", 15))
        ),
        injection_batch_size=int(runtime_settings.get("injection_batch_size", 3)),
        max_tool_result_chars=int(
            runtime_settings.get("max_tool_result_chars", 50_000)
        ),
        finalize_on_exhaustion=(
            False
            if one_shot
            else bool(runtime_settings.get("finalize_on_exhaustion", True))
        ),
    )
    retry = WorkerRetryPolicy(max_attempts=1 if one_shot else max_attempts)
    return budget, retry


@asynccontextmanager
async def _observed_worker_span(kind: SpanKind, name: str, *, worker_id: str,
                                attempt: int = 1, **attributes):
    context = current_telemetry_context()
    if context is None:
        yield None
        return
    async with global_telemetry.span(
        kind, name, context=context, worker_id=worker_id,
        attempt=attempt, attributes=attributes,
    ) as span:
        yield span


class SpawnWorkerInput(BaseModel):
    agent_name: str = Field(..., description="必须是黄页中存在的技能包名或基础工具名。")
    directive: str = Field(..., description="详尽的操作指令，需包含所有参数。")
    model: str = Field(default="", description="可选模型覆盖，如 deepseek-v3.2、doubao-1-6-flash。留空则走默认解析链。")
    join: bool = Field(default=True, description="是否等待该 Worker 的结果后再继续 Coordinator。")
    wait_mode: WorkerWaitMode = Field(default="all", description="等待策略：all、any 或 quorum。")
    quorum: int = Field(default=1, ge=1, description="quorum 模式所需的完成数量。")
    wait_timeout_s: float = Field(default=60.0, gt=0, le=600, description="最长等待秒数。")
    max_turns: int = Field(default=6, ge=1, le=50, description="Worker 最大 ReAct 轮数。")
    max_duration_s: float = Field(default=60.0, gt=0, le=1800, description="Worker 单次尝试最长执行秒数。")
    max_tokens: int = Field(default=32000, ge=1, description="Worker 单次尝试最大 Token 数。")
    max_tool_calls: int = Field(default=12, ge=0, le=200, description="Worker 单次尝试最大工具调用次数。")
    max_attempts: int = Field(default=3, ge=1, le=5, description="可恢复错误的最大执行尝试次数。")


class SendMessageInput(BaseModel):
    to_agent_id: str = Field(..., description="要继续对话的 Worker 的 Task-ID。")
    message: str = Field(..., description="追加的明确指令或纠正要求。")


async def _execute_sandbox_loop(
    worker_id: str,
    session_id: str,
    messages: list,
    toolset: ToolSet | list,
    model_name: str = "",
    *,
    budget: WorkerResourceBudget | None = None,
    attempt: int = 1,
    context: SessionContext | None = None,
) -> WorkerExecutionResultSpec:
    """Execute one isolated Worker attempt under an explicit resource budget."""
    worker_logger = logger.bind(worker_id=worker_id, session_id=session_id)
    from configs.settings import settings

    runtime_settings = settings.get_agent_runtime("worker")
    if budget is None:
        budget = WorkerResourceBudget(
            max_injections=int(runtime_settings.get("max_injections", 15)),
            injection_batch_size=int(runtime_settings.get("injection_batch_size", 3)),
            max_tool_result_chars=int(runtime_settings.get("max_tool_result_chars", 50_000)),
            finalize_on_exhaustion=bool(runtime_settings.get("finalize_on_exhaustion", True)),
        )
    if not isinstance(toolset, ToolSet):
        toolset = physical_tool_manager.get_worker_toolset(
            allowed_names=[item.name for item in toolset],
            session_id=session_id,
            profile="legacy",
        )
    base_llm = get_worker_llm(tool_specified_model=model_name) if model_name else get_tool_llm()
    llm = base_llm
    if toolset.tools:
        llm = llm.bind_tools(toolset.tools)
    start_time = time.time()
    total_tokens_used = 0
    tool_uses_count = 0
    injection_count = 0
    fallback_context, fallback_output = settings.get_context_limits(model_name or None)
    context_window = getattr(llm, "context_window_tokens", fallback_context)
    output_reserve = getattr(llm, "max_output_tokens", fallback_output)
    context_budget = build_context_budget(
        context_window=context_window,
        output_reserve=output_reserve,
        tools=toolset.tools,
    )
    parent_context = context or SessionContext(session_id=session_id)
    if parent_context.session_id != session_id:
        raise ValueError("Worker context must belong to the parent session")
    session_context = parent_context.model_copy(
        update={"channel": "worker", "agent_id": worker_id}
    )
    tool_config: RunnableConfig = {
        "configurable": {
            "thread_id": session_context.session_id,
            "worker_id": worker_id,
            "user_id": session_context.user_id,
            "workspace_id": session_context.workspace_id,
            "channel": session_context.channel,
        }
    }

    def result(
        *,
        status: str,
        summary: str,
        termination_reason: str,
        output: str | None = None,
        error: WorkerErrorSpec | None = None,
    ) -> WorkerExecutionResultSpec:
        completed_at = time.time()
        duration_ms = int((completed_at - start_time) * 1000)
        execution = WorkerExecutionResultSpec(
            status=status,
            summary=summary,
            output=output,
            error=error,
            usage=WorkerUsageSpec(
                total_tokens=total_tokens_used,
                tool_uses=tool_uses_count,
                duration_ms=duration_ms,
            ),
            timing=WorkerTimingSpec(
                started_at=start_time,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
            termination_reason=termination_reason,
            attempt=attempt,
        )
        global_telemetry.event(
            "agent.run.completed",
            level="info" if status == "completed" else "warning",
            payload={
                "role": "worker",
                "worker_id": worker_id,
                "status": status,
                "stop_reason": termination_reason,
                "iterations": min(budget.max_turns, max(0, tool_uses_count + 1)),
                "tokens": total_tokens_used,
                "tool_calls": tool_uses_count,
                "injections": injection_count,
                "duration_ms": duration_ms,
            },
        )
        return execution

    async def query_loop() -> WorkerExecutionResultSpec:
        nonlocal messages, total_tokens_used, tool_uses_count, injection_count

        async def finalize(reason: AgentStopReason) -> str:
            nonlocal total_tokens_used
            if not budget.finalize_on_exhaustion:
                return exhaustion_fallback(reason)
            global_telemetry.event(
                "agent.run.finalizing",
                payload={"role": "worker", "reason": reason.value, "worker_id": worker_id},
            )
            try:
                final_response = await base_llm.ainvoke(
                    [*messages, SystemMessage(content=exhaustion_prompt(reason))]
                )
                total = usage_total(final_response)
                total_tokens_used += total
                content = str(getattr(final_response, "content", "") or "").strip()
                if content:
                    messages.append(final_response)
                    record_sidechain_transcript(session_id, worker_id, [final_response])
                    return content
            except asyncio.CancelledError:
                raise
            except Exception as error:
                worker_logger.warning(
                    "Worker finalization failed", reason=reason.value, error=str(error)
                )
            return exhaustion_fallback(reason)

        global_telemetry.event(
            "agent.run.started",
            payload={"role": "worker", "worker_id": worker_id, "attempt": attempt},
        )
        for _turn in range(budget.max_turns):
            global_telemetry.event(
                "agent.iteration.started",
                payload={"role": "worker", "iteration": _turn + 1, "worker_id": worker_id},
            )
            task_info = global_task_manager.get_active_task(worker_id)
            if task_info:
                remaining_injections = max(0, budget.max_injections - injection_count)
                batch_limit = min(budget.injection_batch_size, remaining_injections)
                drained = 0
                while drained < batch_limit and not task_info.pending_messages.empty():
                    command = task_info.pending_messages.get_nowait()
                    if command.kind == "cancel":
                        raise asyncio.CancelledError
                    interruption_msg = HumanMessage(
                        content=(
                            f"【Coordinator {command.kind} 指令】\n"
                            f"command_id={command.command_id}\n{command.content}\n"
                            "请优先处理此指令！"
                        )
                    )
                    messages.append(interruption_msg)
                    drained += 1
                    injection_count += 1
                    record_sidechain_transcript(session_id, worker_id, [interruption_msg])
                    task_info.pending_messages.task_done()
                    worker_logger.info(
                        "Worker command received",
                        command_id=command.command_id,
                        command_kind=command.kind,
                    )
                if drained:
                    global_telemetry.event(
                        "agent.message.injected",
                        payload={
                            "role": "worker",
                            "worker_id": worker_id,
                            "count": drained,
                            "total": injection_count,
                        },
                    )

            async with _observed_worker_span(
                SpanKind.COMPRESSION, "worker.context.prepare",
                worker_id=worker_id, attempt=attempt,
            ) as compression_span:
                context_view = await global_context_manager.prepare(
                    session_context, messages, context_budget,
                )
                if compression_span is not None:
                    compression_span.annotate(
                        tokens_before=context_view.tokens_before,
                        tokens_after=context_view.tokens_after,
                        tokens_saved=max(0, context_view.tokens_before - context_view.tokens_after),
                        context_window=context_view.context_window,
                        input_limit=context_view.input_limit,
                        output_reserve=context_view.output_reserve,
                        actions=context_view.actions,
                    )
            # ContextManager returns the model-facing state for this Worker.
            # Keep it as the next iteration's source so a later prepare call
            # does not reprocess the pre-compaction history.
            messages = context_view.messages
            response = await llm.ainvoke(context_view.messages)
            messages.append(response)
            if getattr(response, "usage_metadata", None):
                total_tokens_used += usage_total(response)
            record_sidechain_transcript(session_id, worker_id, [response])

            if total_tokens_used > budget.max_tokens:
                final_output = await finalize(AgentStopReason.TOKEN_BUDGET)
                return result(
                    status="failed",
                    summary="Worker exceeded its token budget.",
                    termination_reason="token_budget",
                    output=final_output,
                    error=WorkerErrorSpec(
                        category="budget",
                        message=f"Token budget exceeded: {total_tokens_used}/{budget.max_tokens}",
                        retryable=False,
                    ),
                )
            if not response.tool_calls:
                content = str(response.content or "").strip()
                finish_reason = str(
                    getattr(response, "response_metadata", {}).get("finish_reason", "")
                ).lower()
                if not content:
                    messages.append(
                        SystemMessage(content=global_prompt_runtime.render("retry.empty_response"))
                    )
                    global_telemetry.event(
                        "agent.response.recovering",
                        level="warning",
                        payload={"role": "worker", "reason": "empty_response"},
                    )
                    continue
                if finish_reason in {"length", "max_tokens"}:
                    messages.append(
                        SystemMessage(
                            content=global_prompt_runtime.render(
                                "retry.continue_after_truncation"
                            )
                        )
                    )
                    global_telemetry.event(
                        "agent.response.recovering",
                        payload={"role": "worker", "reason": "length"},
                    )
                    continue
                return result(
                    status="completed",
                    summary="Worker completed the assigned task.",
                    termination_reason="completed",
                    output=content,
                )

            remaining = budget.max_tool_calls - tool_uses_count
            if remaining < len(response.tool_calls):
                final_output = await finalize(AgentStopReason.TOOL_BUDGET)
                return result(
                    status="failed",
                    summary="Worker exceeded its tool-call budget.",
                    termination_reason="tool_budget",
                    output=final_output,
                    error=WorkerErrorSpec(
                        category="budget",
                        message=f"Tool-call budget exceeded: {budget.max_tool_calls}",
                        retryable=False,
                    ),
                )
            tool_uses_count += len(response.tool_calls)
            calls = [(call["name"], call["args"]) for call in response.tool_calls]
            for name, _arguments in calls:
                worker_logger.info("发起工具调用", tool_name=name)
            tool_results = await toolset.execute_many(calls, tool_config)
            for tool_call, execution in zip(response.tool_calls, tool_results, strict=True):
                descriptor = toolset.descriptor(tool_call["name"])
                persist_result = descriptor.persist_result if descriptor else True
                status = "success" if execution.ok else "error"
                if persist_result:
                    tool_msg = ToolMessage(
                        content=execution.to_model_content(
                            max_chars=budget.max_tool_result_chars
                        ),
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        status=status,
                        artifact=execution.model_dump(mode="json"),
                    )
                    persisted_tool_msg = tool_msg
                else:
                    tool_msg = ToolMessage(
                        content=execution.to_model_content(
                            max_chars=budget.max_tool_result_chars
                        ),
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        status=status,
                    )
                    persisted_tool_msg = ToolMessage(
                        content=NON_PERSISTED_TOOL_RESULT_PLACEHOLDER,
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        status=status,
                    )
                messages.append(tool_msg)
                record_sidechain_transcript(
                    session_id, worker_id, [persisted_tool_msg]
                )

        final_output = await finalize(AgentStopReason.MAX_ITERATIONS)
        return result(
            status="failed",
            summary="Worker exhausted its ReAct turn budget.",
            termination_reason="max_turns",
            output=final_output,
            error=WorkerErrorSpec(
                category="budget",
                message=f"Maximum turns reached: {budget.max_turns}",
                retryable=False,
            ),
        )

    try:
        execution = await asyncio.wait_for(query_loop(), timeout=budget.max_duration_s)
        worker_logger.info(
            "Worker attempt finished",
            status=execution.status,
            duration_ms=execution.timing.duration_ms,
            tokens=execution.usage.total_tokens,
            attempt=attempt,
        )
        return execution
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as error:
        return result(
            status="timed_out",
            summary=f"Worker attempt timed out after {budget.max_duration_s:g} seconds.",
            termination_reason="timeout",
            output=exhaustion_fallback(AgentStopReason.MODEL_TIMEOUT),
            error=WorkerErrorSpec(category="timeout", message=str(error) or "timeout", retryable=True),
        )
    except Exception as error:
        category, retryable = classify_worker_error(error)
        worker_logger.exception("Worker attempt failed", category=category, retryable=retryable)
        return result(
            status="failed",
            summary=f"Worker attempt failed: {error}",
            termination_reason="unrecoverable_error",
            output=exhaustion_fallback(AgentStopReason.MODEL_ERROR),
            error=WorkerErrorSpec(
                category=category,
                message=str(error),
                retryable=retryable,
            ),
        )


async def _execute_with_retries(
    worker_id: str,
    execute_attempt: Callable[[int], Awaitable[WorkerExecutionResultSpec]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> WorkerExecutionResultSpec:
    """Run recoverable attempts with exponential backoff and aggregate telemetry."""
    task = global_task_manager.get_active_task(worker_id)
    if task is None:
        raise RuntimeError(f"Worker {worker_id} is not registered")
    policy = task.retry_policy
    base_attempt = task.attempt
    started_at = time.time()
    total_tokens = 0
    total_tools = 0
    final: WorkerExecutionResultSpec | None = None

    for run_index in range(1, policy.max_attempts + 1):
        attempt = base_attempt + run_index - 1
        global_task_manager.set_attempt(worker_id, attempt)
        final = await execute_attempt(attempt)
        total_tokens += final.usage.total_tokens
        total_tools += final.usage.tool_uses
        if final.status == "completed":
            break

        category = final.error.category if final.error else "internal"
        if (
            final.error is None
            or not final.error.retryable
            or not policy.should_retry(category, run_index)
        ):
            if final.error and final.error.retryable and run_index >= policy.max_attempts:
                final = final.model_copy(
                    update={
                        "status": "failed",
                        "summary": f"{final.summary} Retry attempts exhausted.",
                        "termination_reason": "retries_exhausted",
                    }
                )
            break

        delay = policy.delay_for(run_index)
        global_task_manager.transition_task(
            worker_id,
            "retrying",
            "recoverable_failure",
            category=category,
            next_attempt=attempt + 1,
            delay_s=delay,
        )
        await sleep(delay)
        global_task_manager.transition_task(
            worker_id,
            "running",
            "retry_started",
            attempt=attempt + 1,
        )

    if final is None:
        raise RuntimeError("Worker retry loop produced no result")
    completed_at = time.time()
    duration_ms = int((completed_at - started_at) * 1000)
    return final.model_copy(
        update={
            "usage": WorkerUsageSpec(
                total_tokens=total_tokens,
                tool_uses=total_tools,
                duration_ms=duration_ms,
            ),
            "timing": WorkerTimingSpec(
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
            "attempt": task.attempt,
        }
    )


async def _background_task_wrapper(
    worker_id: str,
    session_id: str,
    initial_messages: list,
    toolset: ToolSet,
    model_name: str = "",
    context: SessionContext | None = None,
) -> None:
    """Own the complete Worker lifecycle and publish exactly one terminal result."""
    task = global_task_manager.get_active_task(worker_id)
    execution: WorkerExecutionResultSpec | None = None
    try:
        async with _observed_worker_span(
            SpanKind.WORKER, "worker.queue_wait", worker_id=worker_id,
        ):
            await global_task_manager.acquire_execution_slot(worker_id)
        task = global_task_manager.get_active_task(worker_id)
        if task is None:
            raise asyncio.CancelledError

        async def execute(attempt: int) -> WorkerExecutionResultSpec:
            async with _observed_worker_span(
                SpanKind.WORKER, "worker.attempt", worker_id=worker_id,
                attempt=attempt, model=model_name or "default",
            ) as worker_span:
                result = await _execute_sandbox_loop(
                    worker_id, session_id, initial_messages, toolset, model_name,
                    budget=task.budget, attempt=attempt, context=context,
                )
                if worker_span is not None:
                    worker_span.annotate(
                        termination_reason=result.termination_reason,
                        tool_uses=result.usage.tool_uses,
                    )
                    if result.status != "completed":
                        status = SpanStatus.CANCELLED if result.status == "cancelled" else (
                            SpanStatus.TIMEOUT if result.termination_reason == "timeout" else SpanStatus.ERROR
                        )
                        worker_span.set_status(
                            status,
                            error_kind=result.error.category if result.error else result.termination_reason,
                            error_message=result.error.message if result.error else result.summary,
                        )
                return result

        execution = await _execute_with_retries(worker_id, execute)
    except asyncio.CancelledError:
        now = time.time()
        reason = task.cancellation_reason if task and task.cancellation_reason else "cancelled"
        started_at = (task.started_at or task.created_at) if task else now
        execution = WorkerExecutionResultSpec(
            status="cancelled",
            summary="Worker was cancelled before completing the task.",
            error=WorkerErrorSpec(category="cancelled", message=reason, retryable=False),
            timing=WorkerTimingSpec(
                started_at=started_at,
                completed_at=now,
                duration_ms=int((now - started_at) * 1000),
            ),
            termination_reason="cancelled",
            attempt=task.attempt if task else 1,
        )
    except Exception as error:
        now = time.time()
        started_at = (task.started_at or task.created_at) if task else now
        logger.exception("Worker lifecycle wrapper failed", worker_id=worker_id)
        execution = WorkerExecutionResultSpec(
            status="failed",
            summary=f"Worker lifecycle wrapper failed: {error}",
            error=WorkerErrorSpec(category="internal", message=str(error), retryable=False),
            timing=WorkerTimingSpec(
                started_at=started_at,
                completed_at=now,
                duration_ms=int((now - started_at) * 1000),
            ),
            termination_reason="unrecoverable_error",
            attempt=task.attempt if task else 1,
        )
    finally:
        global_task_manager.release_execution_slot(worker_id)

    task = global_task_manager.get_active_task(worker_id) or task
    if execution is None:
        return
    join = task.join if task else True
    parent_turn_id = task.parent_turn_id if task else ""
    attempt = task.attempt if task else execution.attempt
    global_task_manager.complete_task(worker_id, execution.status, execution)
    try:
        await global_worker_event_bus.publish(
            WorkerCompletedEvent.create(
                session_id=session_id,
                worker_id=worker_id,
                parent_turn_id=parent_turn_id,
                attempt=attempt,
                execution=execution,
                join=join,
            )
        )
    except RuntimeError as error:
        logger.error(
            "Worker result delivery hit backpressure",
            worker_id=worker_id,
            session_id=session_id,
            error=str(error),
        )


@tool("spawn_worker", args_schema=SpawnWorkerInput)
async def spawn_worker(
    agent_name: str,
    directive: str,
    config: RunnableConfig,
    model: str = "",
    join: bool = True,
    wait_mode: WorkerWaitMode = "all",
    quorum: int = 1,
    wait_timeout_s: float = 60.0,
    max_turns: int = 6,
    max_duration_s: float = 60.0,
    max_tokens: int = 32_000,
    max_tool_calls: int = 12,
    max_attempts: int = 3,
) -> str:
    """启动一个新的 Worker 执行任务。使用此工具时，Worker 没有之前的记忆。

    Worker 模型解析优先级（由高到低）：
    1. 声明了 model 的 Worker Profile 固定使用该 preset，不能被参数覆盖；
       若 NLP_AGENT_WORKER_MODEL 与其冲突，则拒绝启动。
    2. 其他 Profile 依次采用 NLP_AGENT_WORKER_MODEL、本函数 model 参数、
       当前会话 model_profile、agents.<name>.model 与 defaults.worker。
    3. 原生联网 preset 仅允许 requires_native_search=true 的 Profile 使用。

    Args:
        agent_name: 技能黄页中存在的技能包名或基础工具名。
        directive: 详尽的操作指令，需包含所有参数。
        config: LangChain RunnableConfig，从中提取 thread_id 作为 session_id。
        model: 可选的模型 preset/model 覆盖；不能覆盖 Profile 固定模型或借用原生联网 preset。

    Returns:
        JSON 字符串（WorkerNotificationSpec），包含 task_id 和 "started" 状态。
    """
    from configs.settings import settings
    runtime_settings = settings.get_agent_runtime("worker")

    session_id = config.get("configurable", {}).get("thread_id", "default_session")
    parent_context = SessionContext.from_config(config, require=True)
    parent_turn_id = config.get("configurable", {}).get("turn_id", "")
    parent_worker_id = config.get("configurable", {}).get("worker_id", "")
    worker_id = create_agent_id(agent_name)

    try:
        profile = skill_loader.resolve_profile(agent_name)
        skill_loader.validate_tool_references(set(physical_tool_manager.runtime.catalog.names()))
        resolved_model = _resolve_profile_worker_model(
            profile,
            requested_model=model,
            model_profile=config.get("configurable", {}).get("model_profile"),
        )
        toolset = physical_tool_manager.get_worker_toolset(
            allowed_names=profile.allowed_tools,
            capabilities=profile.capabilities,
            denied_names=profile.denied_tools,
            session_id=session_id,
            profile=profile.name,
            inherit_policy=profile.inherit_tool_policy,
        )
        if profile.execution_mode == "one_shot" and toolset.tools:
            raise ValueError(
                f"one-shot Worker Profile {profile.name!r} cannot receive tools"
            )
        max_duration_s, wait_timeout_s = _aligned_worker_timeouts(
            toolset,
            max_duration_s=max_duration_s,
            wait_timeout_s=wait_timeout_s,
            join=join,
        )
        worker_budget, worker_retry = _build_profile_execution_policies(
            profile,
            runtime_settings=runtime_settings,
            max_turns=max_turns,
            max_duration_s=max_duration_s,
            max_tokens=max_tokens,
            max_tool_calls=max_tool_calls,
            max_attempts=max_attempts,
        )
    except ValueError as error:
        return WorkerNotificationSpec(
            task_id=worker_id, status="failed", summary=str(error)
        ).model_dump_json(exclude_none=True)
    sop_prompt = profile.system_prompt

    import datetime
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S %A')
    skill_section = f"[专家领域与标准操作流程 (SOP)]\n{sop_prompt}\n[/专家领域与标准操作流程 (SOP)]"
    system_instruction = global_prompt_runtime.composer.compose(
        [("worker", {"today": current_time}), skill_section]
    )

    initial_messages = [
        SystemMessage(content=system_instruction),
        HumanMessage(content=f"【任务指令】：\n{directive}")
    ]

    write_agent_metadata(session_id, worker_id, {
        "agentType": agent_name,
        "directive": directive,
        "model": resolved_model,
        "profile": profile.name,
        "skills": list(profile.skills),
        "toolGrant": toolset.snapshot.model_dump(mode="json"),
        "join": join,
        "parentTurnId": parent_turn_id,
        "waitMode": wait_mode,
        "quorum": quorum,
        "waitTimeoutS": wait_timeout_s,
        "parentWorkerId": parent_worker_id,
        "budget": {
            "maxTurns": worker_budget.max_turns,
            "maxDurationS": worker_budget.max_duration_s,
            "maxTokens": worker_budget.max_tokens,
            "maxToolCalls": worker_budget.max_tool_calls,
        },
        "retry": {"maxAttempts": worker_retry.max_attempts},
    })
    # Keep orchestration evidence queryable without putting the directive itself
    # (which may contain user data) into observability storage.
    global_telemetry.event(
        "agent.worker.dispatched",
        payload={
            "worker_id": worker_id,
            "agent_name": agent_name,
            "join": join,
            "wait_mode": wait_mode,
            "quorum": quorum,
            "directive_chars": len(directive),
            "directive_sha256": hashlib.sha256(directive.encode("utf-8")).hexdigest(),
        },
    )
    record_sidechain_transcript(session_id, worker_id, initial_messages)

    bg_task = asyncio.create_task(
        _background_task_wrapper(
            worker_id,
            session_id,
            initial_messages,
            toolset,
            resolved_model,
            parent_context,
        )
    )

    global_task_manager.register_task(
        task_id=worker_id,
        task_type=agent_name,
        command=directive,
        future=bg_task,
        session_id=session_id,
        join=join,
        parent_turn_id=parent_turn_id,
        parent_worker_id=parent_worker_id,
        wait_mode=wait_mode,
        quorum=quorum,
        wait_timeout_s=wait_timeout_s,
        budget=worker_budget,
        retry_policy=worker_retry,
    )

    return WorkerNotificationSpec(
        task_id=worker_id,
        status="started",
        summary="任务已在后台启动。如需终止，请使用 TaskStop 工具。",
        join=join,
    ).model_dump_json(exclude_none=True)


@tool("send_message", args_schema=SendMessageInput)
async def send_message(to_agent_id: str, message: str, config: RunnableConfig) -> str:
    """向正在运行的 Worker 追加指令，不恢复或重新启动已结束的 Worker。

    Args:
        to_agent_id: 目标 Worker 的 Task-ID。
        message: 追加的明确指令或纠正要求。
        config: LangChain RunnableConfig，从中提取 thread_id 作为 session_id。

    Returns:
        JSON 字符串（WorkerNotificationSpec），包含 task_id 和状态摘要。
    """
    session_id = config.get("configurable", {}).get("thread_id", "default_session")

    # Worker 正在运行：插队投递，不重启。
    task = global_task_manager.get_active_task(to_agent_id)
    if task:
        accepted = global_task_manager.queue_command(
            WorkerCommand.create(
                session_id=session_id,
                worker_id=to_agent_id,
                kind="continue",
                content=message,
            )
        )
        if not accepted:
            return WorkerNotificationSpec(
                task_id=to_agent_id,
                status="failed",
                summary="Worker mailbox is full; retry later.",
            ).model_dump_json(exclude_none=True)
        return WorkerNotificationSpec(
            task_id=to_agent_id,
            status="started",
            summary="消息已投递至运行中的 Worker，将在其下一轮工具调用前处理。"
        ).model_dump_json(exclude_none=True)

    return WorkerNotificationSpec(
        task_id=to_agent_id,
        status="failed",
        summary="Worker 已结束或不存在，不能重新启动。请由 Coordinator 综合现有结果，或创建新的 Worker。",
    ).model_dump_json(exclude_none=True)
