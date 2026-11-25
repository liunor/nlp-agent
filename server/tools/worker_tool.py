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
from contextvars import ContextVar
import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from core.task_manager import global_task_manager
from core.worker_events import WorkerCompletedEvent, global_worker_event_bus
from core.worker_lifecycle import (
    WorkerResourceBudget,
    WorkerRetryPolicy,
    classify_worker_error,
    detached_worker_allowed,
)
from gateway.execution_fence import current_turn_write_fence
from core.worker_protocol import WorkerCommand, WorkerWaitMode
from core.vision_execution import current_image_turn
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
from core.tool_runtime import ToolExecutionResult, ToolGrantSnapshot, ToolSet
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
from core.model_runtime.usage import (
    bind_usage_attribution,
    worker_usage_attribution,
)
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
_WORKER_TIME_REFERENCE = "由后续运行时上下文消息提供"


def _build_worker_stable_prefix() -> SystemMessage:
    """Build the versioned Worker prefix without accepting runtime inputs.

    Keeping this boundary argument-free makes it difficult for future per-run
    fields (time, profile SOP, directive, identifiers) to enter the cacheable
    prefix accidentally. A prompt version/configuration change is allowed to
    invalidate the prefix intentionally.
    """
    return SystemMessage(
        content=global_prompt_runtime.render(
            "worker",
            today=_WORKER_TIME_REFERENCE,
        )
    )


def _build_worker_initial_messages(
    sop_prompt: str,
    directive: str,
    *,
    current_time: str | None = None,
    budget: WorkerResourceBudget | None = None,
) -> list[SystemMessage | HumanMessage]:
    """Keep the argument-free Worker prefix ahead of every per-run field."""
    resolved_time = current_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
    skill_section = (
        "[专家领域与标准操作流程 (SOP)]\n"
        f"{sop_prompt}\n"
        "[/专家领域与标准操作流程 (SOP)]"
    )
    context_lines = [f"当前真实时间：{resolved_time}"]
    if budget is not None and budget.max_tool_calls > 0:
        context_lines.append(
            f"执行预算上限：最多 {budget.max_turns} 轮推理，累计最多 {budget.max_tool_calls} 次工具调用"
            "（配额用满后将直接进入无工具总结阶段，请精炼查询并合理控制单轮并行调用数量）"
        )
    runtime_context = (
        "[运行时上下文]\n"
        + "\n".join(context_lines)
        + "\n[/运行时上下文]"
    )
    return [
        _build_worker_stable_prefix(),
        SystemMessage(content=skill_section),
        SystemMessage(content=runtime_context),
        HumanMessage(content=f"【任务指令】：\n{directive}"),
    ]


_TOOL_RUNTIME_OVERHEAD_S = 30.0
_JOIN_COMPLETION_GRACE_S = 5.0


def _aligned_worker_timeouts(
    toolset: ToolSet,
    *,
    max_duration_s: float,
    wait_timeout_s: float,
    join: bool,
    expected_image_calls: int = 1,
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
            (descriptor.timeout_s * retry.max_attempts + retry_delays_s)
            * (
                (max(1, expected_image_calls) + max(1, descriptor.max_concurrency) - 1)
                // max(1, descriptor.max_concurrency)
                if descriptor.name == "image_analyze" else 1
            ),
        )

    required_duration_s = worst_tool_runtime_s + _TOOL_RUNTIME_OVERHEAD_S
    visual = toolset.has("image_analyze")
    if required_duration_s <= max_duration_s and not visual:
        return max_duration_s, wait_timeout_s
    if required_duration_s > 1_800:
        raise ValueError("granted tool timeouts exceed the Worker duration limit")

    required_duration_s = max(max_duration_s, required_duration_s)
    if visual and (turn := current_image_turn()) is not None and join:
        required_duration_s = min(required_duration_s, turn.worker_remaining_s() - _JOIN_COMPLETION_GRACE_S)
        required_duration_s = max(0.1, required_duration_s)
    aligned_wait_s = wait_timeout_s
    if join:
        aligned_wait_s = max(
            wait_timeout_s, required_duration_s + _JOIN_COMPLETION_GRACE_S
        )
        if visual and (turn := current_image_turn()) is not None:
            aligned_wait_s = min(aligned_wait_s, turn.worker_remaining_s())
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


_DEFAULT_SPAWN_MAX_TURNS = 6
_DEFAULT_SPAWN_MAX_TOOL_CALLS = 12
_SEARCH_WORKER_DEFAULT_MAX_TURNS = 5
_SEARCH_WORKER_DEFAULT_MAX_TOOL_CALLS = 6
_SEARCH_WORKER_MAX_TURNS_CAP = 8
_SEARCH_WORKER_MAX_TOOL_CALLS_CAP = 8


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
    search_worker = not one_shot and (
        profile.name == "web_researcher"
        or "web.search" in profile.capabilities
        or "web_search" in profile.allowed_tools
    )
    if one_shot:
        effective_max_turns = 1
        effective_max_tool_calls = 0
    elif search_worker:
        effective_max_turns = (
            _SEARCH_WORKER_DEFAULT_MAX_TURNS
            if max_turns == _DEFAULT_SPAWN_MAX_TURNS
            else min(max_turns, _SEARCH_WORKER_MAX_TURNS_CAP)
        )
        effective_max_tool_calls = (
            _SEARCH_WORKER_DEFAULT_MAX_TOOL_CALLS
            if max_tool_calls == _DEFAULT_SPAWN_MAX_TOOL_CALLS
            else min(max_tool_calls, _SEARCH_WORKER_MAX_TOOL_CALLS_CAP)
        )
    else:
        effective_max_turns = max_turns
        effective_max_tool_calls = max_tool_calls
    budget = WorkerResourceBudget(
        max_turns=effective_max_turns,
        max_duration_s=max_duration_s,
        max_tokens=max_tokens,
        max_tool_calls=effective_max_tool_calls,
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


_worker_model_role: ContextVar[str | None] = ContextVar("worker_model_role", default=None)
_worker_model_request: ContextVar[str | None] = ContextVar("worker_model_request", default=None)


_BUDGET_SKIPPED_TOOL_MESSAGE = (
    "工具调用未执行：已达到本轮工具调用或运行预算上限，请直接基于上方已获取的工具结果输出最终结论。"
)
_CONVERGENCE_SYSTEM_PROMPT = (
    "【运行时收敛提示】本轮工具调用配额或推理轮次已达上限。"
    "请勿再发起任何工具调用，直接基于上方已获取的真实工具结果与核验事实，"
    "严格按要求格式输出完整结论与真实来源 URL。"
)
_PROGRESS_HEARTBEAT_INTERVAL_S = 4.0
_PROGRESS_HEARTBEAT_TICK_S = 1.0
_MAX_PROGRESS_HEARTBEATS = 12
_INTERNAL_COMMENTARY_MARKERS = (
    "执行范围：",
    "执行范围:",
    "[internal",
    "spawn_worker",
    "coordinator",
    "worker",
    "task_id",
)


def _initial_profile_progress(profile_name: str) -> tuple[str, str]:
    if profile_name == "web_researcher":
        return "search", "正在拆解问题并准备联网检索…"
    if profile_name == "web_reader":
        return "fetch", "正在准备连接并读取指定网页…"
    if profile_name == "visual_researcher":
        return "tool", "正在准备解析上传图片…"
    return "tool", "正在准备分析与计算环境…"


def _extract_commentary_text(raw_content: Any) -> str | None:
    text = str(raw_content or "").strip()
    if not text or "```" in text:
        return None
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first_line = re.sub(r"^[#*>\-\d.\s]+", "", first_line).strip()
    if not first_line:
        return None
    lowered = first_line.lower()
    if any(marker in lowered for marker in _INTERNAL_COMMENTARY_MARKERS):
        return None
    if len(first_line) > 90:
        return f"{first_line[:89]}…"
    return first_line


def _clean_host(raw_url: Any) -> str:
    try:
        host = (urlsplit(str(raw_url or "").strip()).hostname or "").lower()
    except ValueError:
        host = ""
    return host.removeprefix("www.")


def _format_tool_start_progress(
    tool_name: str, args: Mapping[str, Any] | None
) -> tuple[str, str, str]:
    safe_args = args or {}
    if tool_name == "web_search":
        query = str(safe_args.get("query") or "").strip()[:56]
        return (
            "search",
            f"正在搜索「{query}」" if query else "正在联网检索公开资料…",
            query or "相关资料",
        )
    if tool_name == "web_fetch":
        host = _clean_host(safe_args.get("url")) or "目标网页"
        return ("fetch", f"正在阅读网页 {host}", host)
    if tool_name == "academic_search":
        query = str(safe_args.get("query") or safe_args.get("query_en") or "").strip()[:56]
        return (
            "search",
            f"正在检索学术文献「{query}」" if query else "正在检索学术文献…",
            query or "学术文献",
        )
    if tool_name == "image_analyze":
        return ("tool", "正在识别与解析图片内容…", "图片")
    return ("tool", "正在执行数据计算与核验…", tool_name)


def _format_tool_end_progress(
    tool_name: str,
    args: Mapping[str, Any] | None,
    execution: ToolExecutionResult,
) -> tuple[str, str, str, str | None]:
    safe_args = args or {}
    if tool_name == "web_search":
        query = str(safe_args.get("query") or "").strip()[:56]
        label = f"联网搜索：「{query}」" if query else "联网搜索"
        if not execution.ok:
            return ("search", "error", label, "检索暂时受限")
        payload: Mapping[str, Any] = {}
        if isinstance(execution.output, str):
            try:
                parsed = json.loads(execution.output)
                if isinstance(parsed, Mapping):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
        elif isinstance(execution.output, Mapping):
            payload = execution.output
        results = payload.get("results")
        if isinstance(results, list) and results:
            hosts: list[str] = []
            for item in results:
                if isinstance(item, Mapping):
                    host = _clean_host(item.get("url"))
                    if host and host not in hosts:
                        hosts.append(host)
            if hosts:
                preview = ", ".join(hosts[:3]) + (" 等" if len(hosts) > 3 else "")
                detail = f"命中 {len(results)} 条 · {preview}"
            else:
                detail = f"命中 {len(results)} 条结果"
            return ("search", "completed", label, detail)
        return ("search", "completed", label, "未命中直接结果，继续调整检索词")

    if tool_name == "web_fetch":
        host = _clean_host(safe_args.get("url")) or "目标网页"
        label = f"阅读网页：{host}"
        if execution.ok:
            title = ""
            if isinstance(execution.output, str):
                try:
                    parsed = json.loads(execution.output)
                    if isinstance(parsed, Mapping):
                        title = str(parsed.get("title") or "").strip()
                except json.JSONDecodeError:
                    title = ""
            if len(title) > 34:
                title = f"{title[:33]}…"
            return ("fetch", "completed", label, title or "已提取正文")
        code = execution.error.code if execution.error else ""
        hint = (
            "站点需人机验证，改用检索摘要或其他来源"
            if code == "cloudflare_challenge"
            else "页面读取受限，改用其他来源"
        )
        return ("fetch", "completed", label, hint)

    if tool_name == "image_analyze":
        return (
            "tool",
            "completed" if execution.ok else "error",
            "图片内容解析",
            "已完成识别" if execution.ok else "识别受限",
        )
    return (
        "tool",
        "completed" if execution.ok else "error",
        "数据计算与核验",
        "已完成" if execution.ok else "执行失败",
    )


class _WorkerProgressReporter:
    """Emit structured Codex-style search steps, commentary, and 4s heartbeats."""

    def __init__(
        self,
        *,
        session_id: str,
        parent_turn_id: str,
        worker_id: str,
        profile_name: str,
        heartbeat_interval_s: float = _PROGRESS_HEARTBEAT_INTERVAL_S,
    ) -> None:
        self.session_id = session_id
        self.parent_turn_id = parent_turn_id
        self.worker_id = worker_id
        self.profile_name = profile_name
        self.heartbeat_interval_s = heartbeat_interval_s
        self.enabled = bool(session_id and parent_turn_id)
        now = time.monotonic()
        self.started_at = now
        self.last_emit_at = now
        self.init_step_id = f"{worker_id}:init"
        self.init_closed = False
        init_kind, init_label = _initial_profile_progress(profile_name)
        self.active_step_id: str | None = self.init_step_id
        self.active_kind: str = init_kind
        self.active_target: str = ""
        self.active_started_at: float = now
        self.completed_searches = 0
        self.completed_fetches = 0
        self.heartbeat_count = 0
        self._init_label = init_label
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.last_emit_at = time.monotonic()
        await global_worker_event_bus.publish_progress(
            session_id=self.session_id,
            parent_turn_id=self.parent_turn_id,
            payload=payload,
        )

    async def close_init_if_needed(self) -> None:
        if not self.enabled or self.init_closed:
            return
        self.init_closed = True
        done_label = (
            "已完成检索方向规划"
            if self.profile_name == "web_researcher"
            else "已完成任务准备"
        )
        await self._send(
            {
                "phase": "step",
                "step_id": self.init_step_id,
                "step_kind": self.active_kind,
                "step_status": "completed",
                "label": done_label,
            }
        )

    async def emit_commentary(self, turn_index: int, text: str) -> None:
        if not self.enabled:
            return
        await self.close_init_if_needed()
        await self._send(
            {
                "phase": "step",
                "step_id": f"{self.worker_id}:commentary:{turn_index}",
                "step_kind": "commentary",
                "step_status": "completed",
                "label": text,
            }
        )

    async def emit_tool_start(
        self, call_id: str, tool_name: str, args: Mapping[str, Any] | None
    ) -> None:
        if not self.enabled:
            return
        await self.close_init_if_needed()
        kind, label, target = _format_tool_start_progress(tool_name, args)
        step_id = f"{self.worker_id}:call:{call_id}"
        self.active_step_id = step_id
        self.active_kind = kind
        self.active_target = target
        self.active_started_at = time.monotonic()
        await self._send(
            {
                "phase": "step",
                "step_id": step_id,
                "step_kind": kind,
                "step_status": "running",
                "label": label,
            }
        )

    async def emit_tool_end(
        self,
        call_id: str,
        tool_name: str,
        args: Mapping[str, Any] | None,
        execution: ToolExecutionResult,
    ) -> None:
        if not self.enabled:
            return
        kind, status, label, detail = _format_tool_end_progress(
            tool_name, args, execution
        )
        if tool_name == "web_search" and execution.ok:
            self.completed_searches += 1
        elif tool_name == "web_fetch" and execution.ok:
            self.completed_fetches += 1
        step_id = f"{self.worker_id}:call:{call_id}"
        payload: dict[str, Any] = {
            "phase": "step",
            "step_id": step_id,
            "step_kind": kind,
            "step_status": status,
            "label": label,
        }
        if detail:
            payload["detail"] = detail
        await self._send(payload)
        self.active_step_id = None
        self.active_kind = "thinking"
        self.active_target = ""
        self.active_started_at = time.monotonic()

    async def emit_synthesizing(self) -> None:
        if not self.enabled:
            return
        await self.close_init_if_needed()
        step_id = f"{self.worker_id}:synthesis"
        self.active_step_id = step_id
        self.active_kind = "synthesis"
        self.active_started_at = time.monotonic()
        await self._send(
            {
                "phase": "step",
                "step_id": step_id,
                "step_kind": "synthesis",
                "step_status": "running",
                "label": "正在汇总并核验已检索到的来源信息…",
            }
        )

    async def finish(self) -> None:
        if not self.enabled:
            return
        await self.close_init_if_needed()
        if self.active_kind == "synthesis" and self.active_step_id:
            await self._send(
                {
                    "phase": "step",
                    "step_id": self.active_step_id,
                    "step_kind": "synthesis",
                    "step_status": "completed",
                    "label": "已完成检索来源核验与信息汇总",
                }
            )
            self.active_step_id = None

    def _heartbeat_summary(self, now: float) -> str:
        total_elapsed = max(1, int(now - self.started_at))
        step_elapsed = max(1, int(now - self.active_started_at))
        if self.active_kind == "fetch":
            host = self.active_target or "目标站点"
            return f"正在等待 {host} 响应并提取正文（已用时 {step_elapsed}s）…"
        if self.active_kind == "search" and self.active_step_id != self.init_step_id:
            target = self.active_target or "公开资料"
            return f"正在检索「{target}」相关来源（已用时 {step_elapsed}s）…"
        if self.active_kind == "synthesis":
            return f"正在交叉核对检索结果并整理引用来源（已用时 {total_elapsed}s）…"
        if self.completed_searches > 0:
            return (
                f"已完成 {self.completed_searches} 次检索，正在比对权威报道与关键事实"
                f"（已用时 {total_elapsed}s）…"
            )
        return f"正在分析问题并构造检索关键词（已用时 {total_elapsed}s）…"

    def start_heartbeat(self) -> None:
        if not self.enabled or self._heartbeat_task is not None:
            return

        async def _loop() -> None:
            tick = min(_PROGRESS_HEARTBEAT_TICK_S, self.heartbeat_interval_s)
            while self.heartbeat_count < _MAX_PROGRESS_HEARTBEATS:
                await asyncio.sleep(tick)
                now = time.monotonic()
                if now - self.last_emit_at < self.heartbeat_interval_s:
                    continue
                self.heartbeat_count += 1
                summary = self._heartbeat_summary(now)
                payload: dict[str, Any] = {
                    "phase": "heartbeat",
                    "step_kind": self.active_kind,
                    "step_status": "running",
                    "summary": summary,
                }
                if self.active_step_id:
                    payload["step_id"] = self.active_step_id
                await self._send(payload)

        self._heartbeat_task = asyncio.create_task(_loop())

    async def stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _repair_dangling_tool_calls(
    messages: list,
    *,
    reason_text: str = _BUDGET_SKIPPED_TOOL_MESSAGE,
) -> tuple[list, list[ToolMessage]]:
    """Ensure every AIMessage with tool_calls is paired with ToolMessages in order."""
    answered_ids = {
        str(getattr(msg, "tool_call_id", ""))
        for msg in messages
        if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None)
    }
    repaired: list = []
    inserted: list[ToolMessage] = []
    for msg in messages:
        repaired.append(msg)
        raw_calls = getattr(msg, "tool_calls", None) or []
        for call in raw_calls:
            call_id = str(call.get("id") or "")
            if not call_id or call_id in answered_ids:
                continue
            answered_ids.add(call_id)
            synthetic = ToolMessage(
                content=reason_text,
                tool_call_id=call_id,
                name=str(call.get("name") or ""),
                status="error",
            )
            repaired.append(synthetic)
            inserted.append(synthetic)
    return repaired, inserted


def _sandbox_worker_llm(model_name: str):
    role = _worker_model_role.get()
    if role is not None:
        return get_worker_llm(agent_name=role, tool_specified_model=_worker_model_request.get())
    return get_worker_llm(tool_specified_model=model_name) if model_name else get_tool_llm()


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
    parent_turn_id: str = "",
    heartbeat_interval_s: float = _PROGRESS_HEARTBEAT_INTERVAL_S,
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
    base_llm = _sandbox_worker_llm(model_name)
    llm = base_llm
    if toolset.tools:
        llm = llm.bind_tools(toolset.tools)
    start_time = time.time()
    total_tokens_used = 0
    tool_uses_count = 0
    successful_tool_uses = 0
    injection_count = 0
    image_results: dict[str, ToolExecutionResult] = {}
    active_task_info = global_task_manager.get_active_task(worker_id)
    effective_turn_id = parent_turn_id or (
        active_task_info.parent_turn_id if active_task_info else ""
    )
    profile_name = getattr(toolset.snapshot, "profile", "") or (
        active_task_info.task_type if active_task_info else ""
    )
    progress = _WorkerProgressReporter(
        session_id=session_id,
        parent_turn_id=effective_turn_id,
        worker_id=worker_id,
        profile_name=profile_name,
        heartbeat_interval_s=heartbeat_interval_s,
    )

    def image_partial_output() -> str:
        limit = max(512, budget.max_tool_result_chars // max(1, len(image_results)))
        return "图片处理结果（逐图核验成功、降级和失败状态；未完成部分不可编造）：\n" + "\n\n".join(
            f"图片 {index}: {name}\n{item.to_model_content(max_chars=limit)}"
            for index, (name, item) in enumerate(image_results.items(), 1)
        )
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
        nonlocal messages, total_tokens_used, tool_uses_count, successful_tool_uses, injection_count
        convergence_injected = False

        async def finalize(reason: AgentStopReason) -> str:
            nonlocal messages, total_tokens_used
            if not budget.finalize_on_exhaustion:
                return exhaustion_fallback(reason)
            messages, repaired_tools = _repair_dangling_tool_calls(messages)
            if repaired_tools:
                record_sidechain_transcript(session_id, worker_id, repaired_tools)
            await progress.emit_synthesizing()
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

            force_synthesis = bool(
                toolset.tools
                and successful_tool_uses > 0
                and (
                    tool_uses_count >= budget.max_tool_calls
                    or _turn == budget.max_turns - 1
                )
            )
            if force_synthesis and not convergence_injected:
                convergence_injected = True
                convergence_msg = SystemMessage(content=_CONVERGENCE_SYSTEM_PROMPT)
                messages.append(convergence_msg)
                record_sidechain_transcript(session_id, worker_id, [convergence_msg])
                await progress.emit_synthesizing()

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
            active_turn_llm = base_llm if force_synthesis else llm
            response = await active_turn_llm.ainvoke(context_view.messages)
            messages.append(response)
            if getattr(response, "usage_metadata", None):
                total_tokens_used += usage_total(response)
            record_sidechain_transcript(session_id, worker_id, [response])

            content = str(response.content or "").strip()
            finish_reason = str(
                getattr(response, "response_metadata", {}).get("finish_reason", "")
            ).lower()
            if not response.tool_calls and content and finish_reason not in {"length", "max_tokens"}:
                return result(
                    status="completed",
                    summary="Worker completed the assigned task.",
                    termination_reason="completed",
                    output=content,
                )

            if total_tokens_used >= budget.max_tokens:
                # Provider usage arrives with the response. A tools-free
                # finalization would make another call after exhaustion.
                messages, repaired_tools = _repair_dangling_tool_calls(messages)
                if repaired_tools:
                    record_sidechain_transcript(session_id, worker_id, repaired_tools)
                return result(
                    status="failed",
                    summary="Worker reached its token budget.",
                    termination_reason="token_budget",
                    output=(
                        image_partial_output() if image_results
                        else exhaustion_fallback(AgentStopReason.TOKEN_BUDGET)
                    ),
                    error=WorkerErrorSpec(
                        category="budget",
                        message=f"Token budget reached: {total_tokens_used}/{budget.max_tokens}",
                        retryable=False,
                    ),
                )
            if not response.tool_calls:
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
                messages.append(
                    SystemMessage(
                        content=global_prompt_runtime.render("retry.continue_after_truncation")
                    )
                )
                global_telemetry.event(
                    "agent.response.recovering",
                    payload={"role": "worker", "reason": "length"},
                )
                continue

            remaining = max(0, budget.max_tool_calls - tool_uses_count)
            if remaining == 0:
                final_output = await finalize(AgentStopReason.TOOL_BUDGET)
                if (
                    successful_tool_uses > 0
                    and final_output
                    and final_output != exhaustion_fallback(AgentStopReason.TOOL_BUDGET)
                ):
                    return result(
                        status="completed",
                        summary="Worker synthesized gathered evidence at tool-call limit.",
                        termination_reason="completed",
                        output=final_output,
                    )
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

            executable_tool_calls = list(response.tool_calls[:remaining])
            skipped_tool_calls = list(response.tool_calls[remaining:])
            commentary_text = _extract_commentary_text(response.content)
            if commentary_text:
                await progress.emit_commentary(_turn + 1, commentary_text)
            tool_uses_count += len(executable_tool_calls)
            calls = [(call["name"], call["args"]) for call in executable_tool_calls]
            for call in executable_tool_calls:
                worker_logger.info("发起工具调用", tool_name=call["name"])
                await progress.emit_tool_start(
                    str(call.get("id") or call["name"]),
                    call["name"],
                    call.get("args"),
                )
            if calls and all(name == "image_analyze" for name, _ in calls):
                from server.tools.vision.batch import execute_image_batch

                remaining_s = budget.max_duration_s - (time.time() - start_time)
                tool_results = await execute_image_batch(
                    toolset, calls, tool_config, timeout_s=max(0, remaining_s - 20),
                )
                for call, item in zip(executable_tool_calls, tool_results, strict=True):
                    image_results[str(call["args"].get("image", ""))] = item
            else:
                tool_results = await toolset.execute_many(calls, tool_config)
            for tool_call, execution in zip(executable_tool_calls, tool_results, strict=True):
                if execution.ok:
                    successful_tool_uses += 1
                await progress.emit_tool_end(
                    str(tool_call.get("id") or tool_call["name"]),
                    tool_call["name"],
                    tool_call.get("args"),
                    execution,
                )
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
            for skipped_call in skipped_tool_calls:
                skipped_msg = ToolMessage(
                    content=_BUDGET_SKIPPED_TOOL_MESSAGE,
                    tool_call_id=skipped_call["id"],
                    name=skipped_call["name"],
                    status="error",
                )
                messages.append(skipped_msg)
                record_sidechain_transcript(session_id, worker_id, [skipped_msg])

            if image_results and any(
                item.error and item.error.code in {
                    "image_batch_deadline", "provider_quota_exhausted", "provider_auth_failed",
                }
                for item in tool_results
            ):
                return result(
                    status="failed", summary="部分图片未完成，已返回逐图结果。",
                    termination_reason="unrecoverable_error", output=image_partial_output(),
                    error=WorkerErrorSpec(category="tool", message="图片处理需要恢复服务或下一轮继续", retryable=False),
                )

        final_output = await finalize(AgentStopReason.MAX_ITERATIONS)
        if (
            successful_tool_uses > 0
            and final_output
            and final_output != exhaustion_fallback(AgentStopReason.MAX_ITERATIONS)
        ):
            return result(
                status="completed",
                summary="Worker synthesized gathered evidence at turn limit.",
                termination_reason="completed",
                output=final_output,
            )
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

    progress.start_heartbeat()
    try:
        execution = await asyncio.wait_for(query_loop(), timeout=budget.max_duration_s)
        await progress.finish()
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
            output=image_partial_output() if image_results else exhaustion_fallback(AgentStopReason.MODEL_TIMEOUT),
            error=WorkerErrorSpec(category="timeout", message=str(error) or "timeout", retryable=not bool(image_results)),
        )
    except Exception as error:
        category, retryable = classify_worker_error(error)
        worker_logger.exception("Worker attempt failed", category=category, retryable=retryable)
        return result(
            status="failed",
            summary=f"Worker attempt failed: {error}",
            termination_reason="unrecoverable_error",
            output=image_partial_output() if image_results else exhaustion_fallback(AgentStopReason.MODEL_ERROR),
            error=WorkerErrorSpec(
                category=category,
                message=str(error),
                retryable=retryable and not bool(image_results),
            ),
        )
    finally:
        await progress.stop_heartbeat()


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

        worker_attribution = worker_usage_attribution(worker_id)
        if worker_attribution is None:
            execution = await _execute_with_retries(worker_id, execute)
        else:
            with bind_usage_attribution(worker_attribution):
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
    parent_fence = current_turn_write_fence()
    parent_turn_is_fenced = bool(
        parent_fence is not None and parent_fence.turn_id == parent_turn_id
    )
    if not detached_worker_allowed(
        join=join, parent_turn_is_fenced=parent_turn_is_fenced
    ):
        return WorkerNotificationSpec(
            task_id=create_agent_id(agent_name),
            status="failed",
            summary=(
                "当前 Worker 执行没有独立持久生命周期；在课堂 Turn 内启动的后台 Worker "
                "必须设置 join=true，确保父 Turn 完成前等待其结束。"
            ),
            join=join,
        ).model_dump_json(exclude_none=True)
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
            expected_image_calls=(current_image_turn().image_count if current_image_turn() else 1),
        )
        if toolset.has("image_analyze"):
            # A whole-Worker retry repeats successful/paid image calls. Each
            # image reports its own failure or partial result instead.
            max_attempts = 1
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

    initial_messages = _build_worker_initial_messages(
        sop_prompt, directive, budget=worker_budget
    )

    write_agent_metadata(session_id, worker_id, {
        "agentType": agent_name,
        "directive": directive,
        "model": resolved_model,
        "profile": profile.name,
        "kvCachePrefixSha256": hashlib.sha256(
            str(initial_messages[0].content).encode("utf-8")
        ).hexdigest(),
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

    model_role_token = _worker_model_role.set(profile.name)
    explicit_request = profile.model or model.strip()
    model_request_token = _worker_model_request.set(explicit_request if explicit_request != "inherit" else None)
    try:
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
    finally:
        _worker_model_request.reset(model_request_token)
        _worker_model_role.reset(model_role_token)

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

    if join and parent_turn_id:
        init_kind, init_label = _initial_profile_progress(profile.name)
        await global_worker_event_bus.publish_progress(
            session_id=session_id,
            parent_turn_id=parent_turn_id,
            payload={
                "phase": "step",
                "step_id": f"{worker_id}:init",
                "step_kind": init_kind,
                "step_status": "running",
                "label": init_label,
            },
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
