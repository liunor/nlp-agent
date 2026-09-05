"""Coordinator 节点：负责任务拆解、Worker 编排和结果综合。"""

from contextlib import asynccontextmanager
import json

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from core.session_context import SessionContext
from core.learning import ExerciseState, LearningContext, LearningProgress
from core.agent_runtime import (
    AgentRunSnapshot,
    configured_budget,
    exhaustion_fallback,
    exhaustion_prompt,
    global_agent_injections,
    usage_total,
)
from core.coordinator_runtime import invoke_model_with_telemetry
from core.observability.context import TelemetryContext, current_telemetry_context
from core.observability.models import SpanKind
from core.observability.runtime import global_telemetry
from core.prompt_runtime import global_prompt_runtime
from core.skill_loader import skill_loader
from core.tool_registry import physical_tool_manager
from server.agent.llm_factory import get_planner_llm
from server.agent.state import AgentState
from server.memory import global_memory_runtime
from server.tools.task_stop_tool import task_stop_tool
from server.tools.worker_tool import send_message, spawn_worker
from utils.logger import get_logger


logger = get_logger("nlp_agent.coordinator")
_CACHED_SYSTEM_MESSAGE = None
_CACHED_LLM_WITH_TOOLS = None
_CACHED_TOOLSET_KEY = None
_CACHED_SNIP_TOOL = None
_SNIP_APP_REF = None


def _format_knowledge_points(points: object) -> str:
    """Render teacher-authored Markdown as clearly delimited curriculum data."""
    if not isinstance(points, list) or not points:
        return "（该主题暂未配置启用的知识点。）"
    rendered = []
    for index, point in enumerate(points, start=1):
        content = str(point).strip()
        if content:
            rendered.append(f"### 知识点 {index}\n{content}")
    return "\n\n".join(rendered) or "（该主题暂未配置启用的知识点。）"


def _format_blueprint(blueprint: object, *, label: str) -> str:
    """Keep blueprint fields machine-readable and separated from instructions."""
    if not isinstance(blueprint, dict) or not blueprint:
        return f"（当前未分配{label}蓝图。）"
    return "```json\n" + json.dumps(blueprint, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def invalidate_coordinator_caches() -> None:
    """Clear bindings derived from editable prompts, profiles, and tool policy."""
    global _CACHED_SYSTEM_MESSAGE, _CACHED_LLM_WITH_TOOLS, _CACHED_TOOLSET_KEY
    _CACHED_SYSTEM_MESSAGE = None
    _CACHED_LLM_WITH_TOOLS = None
    _CACHED_TOOLSET_KEY = None


@asynccontextmanager
async def _observed_span(kind: SpanKind, name: str, config: RunnableConfig, **attributes):
    context = TelemetryContext.from_config(config) or current_telemetry_context()
    if context is None:
        yield None
        return
    async with global_telemetry.span(kind, name, context=context, attributes=attributes) as span:
        yield span


def _get_system_message() -> SystemMessage:
    global _CACHED_SYSTEM_MESSAGE
    if _CACHED_SYSTEM_MESSAGE is not None:
        return _CACHED_SYSTEM_MESSAGE

    prompt = global_prompt_runtime.render(
        "coordinator", worker_profiles=skill_loader.get_planner_listing()
    )
    _CACHED_SYSTEM_MESSAGE = SystemMessage(content=prompt)
    logger.info("Coordinator system prompt cached", chars=len(prompt))
    return _CACHED_SYSTEM_MESSAGE


def init_snip_tool(app) -> None:
    """绑定依赖当前 LangGraph 实例的 SnipTool。"""

    global _SNIP_APP_REF, _CACHED_SNIP_TOOL
    _SNIP_APP_REF = app
    from server.tools.snip_tool import make_snip_tool

    _CACHED_SNIP_TOOL = make_snip_tool(app)
    physical_tool_manager.register_orchestration_tool(
        _CACHED_SNIP_TOOL, capability="context.manage", replace=True
    )
    invalidate_coordinator_caches()


def get_coordinator_toolset(config: RunnableConfig | None = None):
    tools = [spawn_worker, send_message, task_stop_tool]
    if _CACHED_SNIP_TOOL is not None:
        tools.append(_CACHED_SNIP_TOOL)
    session_id = (config or {}).get("configurable", {}).get("thread_id", "")
    return physical_tool_manager.get_coordinator_toolset(
        tools,
        session_id=session_id,
        allow_high_risk=True,
    )


def _get_llm_with_tools(config: RunnableConfig):
    global _CACHED_LLM_WITH_TOOLS, _CACHED_SNIP_TOOL, _CACHED_TOOLSET_KEY
    toolset = get_coordinator_toolset(config)
    model_profile = config.get("configurable", {}).get("model_profile")
    cache_key = (physical_tool_manager.catalog_revision, toolset.names, model_profile)
    if _CACHED_LLM_WITH_TOOLS is not None and _CACHED_TOOLSET_KEY == cache_key:
        return _CACHED_LLM_WITH_TOOLS
    _CACHED_LLM_WITH_TOOLS = get_planner_llm(model_profile).bind_tools(toolset.tools)
    _CACHED_TOOLSET_KEY = cache_key
    return _CACHED_LLM_WITH_TOOLS


async def coordinator_node(state: AgentState, config: RunnableConfig) -> dict:
    system_message = _get_system_message()
    session = SessionContext.from_config(config, require=True)
    runtime_budget = configured_budget("coordinator")
    turn_id = str(config.get("configurable", {}).get("turn_id", ""))
    if state.get("runtime_turn_id") != turn_id or not state.get("runtime_started_at"):
        runtime = AgentRunSnapshot(turn_id=turn_id)
    else:
        runtime = AgentRunSnapshot(
            turn_id=turn_id,
            started_at=float(state.get("runtime_started_at", 0) or 0),
            iterations=int(state.get("runtime_iterations", 0)),
            tokens=int(state.get("runtime_tokens", 0)),
            tool_calls=int(state.get("runtime_tool_calls", 0)),
            injections=int(state.get("runtime_injections", 0)),
            stop_reason=state.get("runtime_stop_reason"),
        )
    async with _observed_span(SpanKind.MEMORY, "memory.inject", config) as memory_span:
        memory_message = global_memory_runtime.context_message(session)
        if memory_span is not None:
            memory_span.annotate(injected=memory_message is not None)
    messages = [system_message]
    configurable = config.get("configurable", {})
    raw_learning = configurable.get("learning_context")
    if raw_learning:
        learning_context = LearningContext.model_validate(raw_learning)
        learning_progress = LearningProgress.model_validate(configurable.get("learning_progress") or {})
        exercise_state = ExerciseState.model_validate(configurable.get("exercise_state") or {})
        topic = configurable.get("learning_topic") or {}
        topic_policy = global_prompt_runtime.render(
            "learning.topic",
            topic_name=str(topic.get("name") or learning_context.topic),
            topic_description=str(topic.get("description") or "未配置主题说明。"),
            knowledge_points=_format_knowledge_points(topic.get("knowledge_points")),
        )
        mode_values = {
            "guided_session": json.dumps(configurable.get("guided_session") or {}, ensure_ascii=False, indent=2),
            "guided_blueprint": _format_blueprint(configurable.get("guided_blueprint"), label="引导"),
            "exercise_session": exercise_state.model_dump_json(indent=2),
            "exercise_blueprint": _format_blueprint(
                configurable.get("exercise_blueprint"), label="练习"
            ),
            "review_blueprint": _format_blueprint(
                configurable.get("review_blueprint"), label="复习"
            ),
        }
        mode_policy = global_prompt_runtime.render(f"learning.mode.{learning_context.mode}", **{
            key: value for key, value in mode_values.items()
            if key in {"guided_session", "guided_blueprint"} if learning_context.mode == "socratic"
        }) if learning_context.mode == "socratic" else (
            global_prompt_runtime.render("learning.mode.explain") if learning_context.mode == "explain" else
            global_prompt_runtime.render("learning.mode.practice", exercise_session=mode_values["exercise_session"], exercise_blueprint=mode_values["exercise_blueprint"]) if learning_context.mode == "practice" else
            global_prompt_runtime.render("learning.mode.review", review_blueprint=mode_values["review_blueprint"], exercise_session=mode_values["exercise_session"])
        )
        progress_sections = [
            global_prompt_runtime.render(f"learning.level.{learning_context.level}"),
            mode_policy,
        ]
        if learning_context.mode != "socratic":
            progress_sections.append(f"当前学习进度：{learning_progress.model_dump_json(indent=2)}")
        progress_policy = "\n\n".join(progress_sections)
        messages.append(SystemMessage(content=global_prompt_runtime.render(
            "learning.policy", topic_policy=topic_policy, progress_policy=progress_policy
        )))
    if memory_message is not None:
        messages.append(memory_message)
    messages.extend(state.get("messages", []))

    remaining_injections = max(0, runtime_budget.max_injections - runtime.injections)
    injected = await global_agent_injections.drain(
        session.session_id,
        limit=runtime_budget.injection_batch_size,
        remaining_total=remaining_injections,
    )
    if injected:
        messages.extend(injected)
        runtime.injections += len(injected)
        global_telemetry.event(
            "agent.message.injected",
            payload={
                "role": "coordinator",
                "count": len(injected),
                "total": runtime.injections,
            },
        )

    from server.agent.compression.context_manager import global_context_manager
    from utils.tokens import build_context_budget

    state_modifiers = list(injected)
    toolset = get_coordinator_toolset(config)
    active_model = _get_llm_with_tools(config)
    context_window = active_model.context_window_tokens
    output_reserve = active_model.max_output_tokens
    budget = build_context_budget(
        context_window=context_window,
        output_reserve=output_reserve,
        tools=toolset.tools,
    )
    async with _observed_span(SpanKind.COMPRESSION, "context.prepare", config) as compression_span:
        transform = await global_context_manager.prepare(session, messages, budget)
        if compression_span is not None:
            compression_span.annotate(
                tokens_before=transform.tokens_before,
                tokens_after=transform.tokens_after,
                tokens_saved=max(0, transform.tokens_before - transform.tokens_after),
                context_window=transform.context_window,
                input_limit=transform.input_limit,
                output_reserve=transform.output_reserve,
                actions=transform.actions,
                removed_messages=len(transform.removed_message_ids),
            )
    if transform.removed_message_ids:
        from langchain_core.messages import RemoveMessage

        state_modifiers.extend(
            RemoveMessage(id=message_id) for message_id in transform.removed_message_ids
        )
    # A compression layer may replace a message in place (for example a
    # micro-compacted ToolMessage or a Snip marker) without removing its ID.
    # Persist every changed message, not only transforms that also removed IDs.
    for message in transform.messages:
        if message not in messages:
            state_modifiers.append(message)
    messages = transform.messages
    stop_reason = runtime.limit_reached(runtime_budget)
    if stop_reason is not None:
        runtime.stop_reason = stop_reason
        runtime.finalizing = True
        global_telemetry.event(
            "agent.run.finalizing",
            payload={"role": "coordinator", "reason": stop_reason.value},
        )
        try:
            response = await invoke_model_with_telemetry(
                get_planner_llm(configurable.get("model_profile")),
                [*messages, SystemMessage(content=exhaustion_prompt(stop_reason))],
                config,
                name="coordinator.finalize_model",
            )
            runtime.tokens += usage_total(response)
            if not str(getattr(response, "content", "") or "").strip():
                response = AIMessage(content=exhaustion_fallback(stop_reason))
        except Exception as error:
            logger.warning(
                "Coordinator finalization failed",
                reason=stop_reason.value,
                error=str(error),
            )
            response = AIMessage(content=exhaustion_fallback(stop_reason))
        return {
            "messages": [*state_modifiers, response],
            "runtime_turn_id": turn_id,
            "runtime_started_at": runtime.started_at,
            "runtime_iterations": runtime.iterations,
            "runtime_tokens": runtime.tokens,
            "runtime_tool_calls": runtime.tool_calls,
            "runtime_injections": runtime.injections,
            "runtime_continue": False,
            "runtime_wait_for_workers": False,
            "runtime_stop_reason": stop_reason.value,
        }

    runtime.iterations += 1
    global_telemetry.event(
        "agent.iteration.started",
        payload={"role": "coordinator", "iteration": runtime.iterations},
    )
    try:
        response = await invoke_model_with_telemetry(
            active_model, messages, config, name="coordinator.model"
        )
    except Exception as error:
        logger.exception("Coordinator model runtime exhausted", error=str(error))
        global_telemetry.event(
            "agent.run.completed",
            level="error",
            payload={
                "role": "coordinator",
                "stop_reason": "model_error",
                "iterations": runtime.iterations,
            },
        )
        response = AIMessage(content=(
            "模型请求在超时重试和故障转移后仍未恢复，本轮已安全停止。"
            "会话状态与已经完成的工具结果均已保留，可以继续重试。"
        ))
        return {
            "messages": [*state_modifiers, response],
            "runtime_turn_id": turn_id,
            "runtime_started_at": runtime.started_at,
            "runtime_iterations": runtime.iterations,
            "runtime_tokens": runtime.tokens,
            "runtime_tool_calls": runtime.tool_calls,
            "runtime_injections": runtime.injections,
            "runtime_continue": False,
            "runtime_wait_for_workers": False,
            "runtime_stop_reason": "model_error",
        }
    runtime.tokens += usage_total(response)

    result_messages = [*state_modifiers, response]
    should_continue = False
    if not getattr(response, "tool_calls", None):
        content = str(getattr(response, "content", "") or "").strip()
        finish_reason = str(
            getattr(response, "response_metadata", {}).get("finish_reason", "")
        ).lower()
        if not content:
            result_messages.append(
                SystemMessage(content=global_prompt_runtime.render("retry.empty_response"))
            )
            should_continue = True
            global_telemetry.event(
                "agent.response.recovering",
                level="warning",
                payload={"role": "coordinator", "reason": "empty_response"},
            )
        elif finish_reason in {"length", "max_tokens"}:
            result_messages.append(
                SystemMessage(
                    content=global_prompt_runtime.render("retry.continue_after_truncation")
                )
            )
            should_continue = True
            global_telemetry.event(
                "agent.response.recovering",
                payload={"role": "coordinator", "reason": "length"},
            )
        else:
            remaining_injections = max(0, runtime_budget.max_injections - runtime.injections)
            follow_ups = await global_agent_injections.drain(
                session.session_id,
                limit=runtime_budget.injection_batch_size,
                remaining_total=remaining_injections,
            )
            if follow_ups:
                result_messages.extend(follow_ups)
                runtime.injections += len(follow_ups)
                should_continue = True
                global_telemetry.event(
                    "agent.message.injected",
                    payload={
                        "role": "coordinator",
                        "count": len(follow_ups),
                        "total": runtime.injections,
                        "phase": "after_final_response",
                    },
                )

    return {
        "messages": result_messages,
        "runtime_turn_id": turn_id,
        "runtime_started_at": runtime.started_at,
        "runtime_iterations": runtime.iterations,
        "runtime_tokens": runtime.tokens,
        "runtime_tool_calls": runtime.tool_calls,
        "runtime_injections": runtime.injections,
        "runtime_continue": should_continue,
        "runtime_wait_for_workers": False,
        "runtime_stop_reason": None,
    }
