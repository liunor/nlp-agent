"""Single-owner LangGraph engine hosted by the Backend Gateway process."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.coordinator_runtime import CoordinatorRuntime
from core.session_context import SessionContext
from core.learning import ExerciseState, LearningContext, LearningProgress, TeachingMaterials
from core.task_manager import global_task_manager
from core.model_runtime.selection import bind_model_profile, current_model_profile
from core.worker_events import global_worker_event_bus
from server.agent.compression.internal_context import (
    looks_like_internal_output,
    public_transcript_messages,
    sanitize_public_output,
)
from gateway.contracts import GatewayEventType


EngineEventSink = Callable[
    [str, str, GatewayEventType, dict], Awaitable[None]
]


class AgentEngine(Protocol):
    async def start(self, event_sink: EngineEventSink) -> None: ...
    async def run_turn(self, context: SessionContext, turn_id: str, content: str, *, learning_context: LearningContext | None = None, learning_progress: LearningProgress | None = None, exercise_state: ExerciseState | None = None, teaching_materials: TeachingMaterials | None = None, model_profile: str | None = None) -> str: ...
    async def inject(self, context: SessionContext, content: str) -> str | None: ...
    async def cancel_turn(self, context: SessionContext, turn_id: str) -> None: ...
    async def delete_session(self, context: SessionContext) -> None: ...
    async def close(self) -> None: ...


class LangGraphAgentEngine:
    """Own exactly one graph/checkpointer/runtime instance for the process."""

    def __init__(self) -> None:
        self._app = None
        self._connection = None
        self._runtime: CoordinatorRuntime | None = None
        self._event_sink: EngineEventSink | None = None
        self._started = False
        self._session_model_profiles: dict[str, str | None] = {}

    async def start(self, event_sink: EngineEventSink) -> None:
        if self._started:
            return
        from server.agent.grapy import build_agent
        from server.agent.node.coordinator import init_snip_tool

        self._event_sink = event_sink
        self._app, self._connection = await build_agent()
        init_snip_tool(self._app)
        self._runtime = CoordinatorRuntime(global_worker_event_bus, self._invoke)
        self._started = True

    async def _emit(
        self,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict | None = None,
    ) -> None:
        if self._event_sink is not None:
            await self._event_sink(turn_id, session_id, event_type, payload or {})

    async def _invoke(
        self,
        messages,
        context: SessionContext,
        background: bool,
        turn_id: str,
        learning_context: LearningContext | None = None,
        learning_progress: LearningProgress | None = None,
        exercise_state: ExerciseState | None = None,
        teaching_materials: TeachingMaterials | None = None,
    ) -> None:
        if self._app is None:
            raise RuntimeError("Agent engine is not started")
        selected_profile = current_model_profile() or self._session_model_profiles.get(
            context.storage_key
        )
        if selected_profile is not None and current_model_profile() != selected_profile:
            with bind_model_profile(selected_profile):
                await self._invoke(
                    messages,
                    context,
                    background,
                    turn_id,
                    learning_context,
                    learning_progress,
                    exercise_state,
                    teaching_materials,
                )
            return
        config = {
            "recursion_limit": 64,
            "configurable": {
                "thread_id": context.session_id,
                "auth_session_id": context.auth_session_id,
                "turn_id": turn_id,
                "user_id": context.user_id,
                "workspace_id": context.workspace_id,
                "channel": context.channel,
                "model_profile": selected_profile,
                "learning_context": learning_context.model_dump(mode="json") if learning_context else None,
                "learning_progress": learning_progress.model_dump(mode="json") if learning_progress else None,
                "exercise_state": exercise_state.model_dump(mode="json") if exercise_state else None,
                "learning_topic": teaching_materials.learning_topic if teaching_materials else {},
                "exercise_blueprint": teaching_materials.exercise_blueprint if teaching_materials else {},
                "review_blueprint": teaching_materials.review_blueprint if teaching_materials else {},
                "guided_session": teaching_materials.guided_session if teaching_materials else {},
                "guided_blueprint": teaching_materials.guided_blueprint if teaching_materials else {},
            },
        }
        if background:
            await self._emit(
                turn_id,
                context.session_id,
                GatewayEventType.WORKER_UPDATE,
                {"phase": "coordinator_resume"},
            )
        active_tool_node = False
        observed_tool_events = False
        active_tools: dict[str, str] = {}
        suppressed_model_runs: set[str] = set()
        anonymous_suppressed_model_stream = False
        async for event in self._app.astream_events(
            {"messages": messages}, config=config, version="v2"
        ):
            metadata = event.get("metadata", {})
            node = metadata.get("langgraph_node")
            event_name = event.get("event")
            if event_name == "on_chat_model_start" and node == "coordinator":
                anonymous_suppressed_model_stream = False
                continue
            if event_name == "on_chat_model_end" and node == "coordinator":
                anonymous_suppressed_model_stream = False
                continue
            if event_name == "on_chat_model_stream" and node == "coordinator":
                if background:
                    continue
                chunk = event.get("data", {}).get("chunk")
                if not isinstance(chunk, AIMessageChunk):
                    continue
                run_id = str(event.get("run_id") or "")
                if run_id in suppressed_model_runs or (
                    not run_id and anonymous_suppressed_model_stream
                ):
                    continue
                if _is_internal_model_event(metadata):
                    if run_id:
                        suppressed_model_runs.add(run_id)
                    else:
                        anonymous_suppressed_model_stream = True
                    continue
                if chunk.content:
                    if looks_like_internal_output(chunk.content):
                        if run_id:
                            suppressed_model_runs.add(run_id)
                        else:
                            anonymous_suppressed_model_stream = True
                        continue
                    await self._emit(
                        turn_id,
                        context.session_id,
                        GatewayEventType.MESSAGE_DELTA,
                        {"delta": chunk.content, "background": background},
                    )
                reasoning = chunk.additional_kwargs.get("reasoning_content")
                if reasoning and not looks_like_internal_output(reasoning):
                    await self._emit(
                        turn_id,
                        context.session_id,
                        GatewayEventType.MESSAGE_DELTA,
                        {"delta": reasoning, "channel": "reasoning", "background": background},
                    )
            elif event_name == "on_chain_start" and node == "tools" and not active_tool_node:
                active_tool_node = True
                observed_tool_events = False
            elif event_name == "on_tool_start":
                observed_tool_events = True
                tool_id = str(event.get("run_id") or event.get("name") or uuid.uuid4())
                tool_name = str(event.get("name") or "工具")
                active_tools[tool_id] = tool_name
                await self._emit(
                    turn_id,
                    context.session_id,
                    GatewayEventType.TOOL_STARTED,
                    {"name": tool_name},
                )
            elif event_name == "on_tool_end":
                observed_tool_events = True
                tool_id = str(event.get("run_id") or event.get("name") or "")
                tool_name = active_tools.pop(tool_id, str(event.get("name") or "工具"))
                await self._emit(
                    turn_id,
                    context.session_id,
                    GatewayEventType.TOOL_COMPLETED,
                    {"name": tool_name},
                )
            elif event_name == "on_tool_error":
                observed_tool_events = True
                tool_id = str(event.get("run_id") or event.get("name") or "")
                tool_name = active_tools.pop(tool_id, str(event.get("name") or "工具"))
                await self._emit(
                    turn_id,
                    context.session_id,
                    GatewayEventType.TOOL_FAILED,
                    {"name": tool_name},
                )
            elif event_name == "on_chain_end" and node == "tools" and active_tool_node:
                active_tool_node = False
                if not observed_tool_events:
                    await self._emit(
                        turn_id,
                        context.session_id,
                        GatewayEventType.TOOL_COMPLETED,
                        {"name": "tool"},
                    )
            await self._apply_pending_snips(context)

    async def run_turn(
        self,
        context: SessionContext,
        turn_id: str,
        content: str,
        *,
        learning_context: LearningContext | None = None,
        learning_progress: LearningProgress | None = None,
        exercise_state: ExerciseState | None = None,
        teaching_materials: TeachingMaterials | None = None,
        model_profile: str | None = None,
    ) -> str:
        self._session_model_profiles[context.storage_key] = model_profile
        with bind_model_profile(model_profile):
            return await self._run_selected_turn(
                context,
                turn_id,
                content,
                learning_context=learning_context,
                learning_progress=learning_progress,
                exercise_state=exercise_state,
                teaching_materials=teaching_materials,
            )

    async def _run_selected_turn(self, context: SessionContext, turn_id: str, content: str, *, learning_context: LearningContext | None = None, learning_progress: LearningProgress | None = None, exercise_state: ExerciseState | None = None, teaching_materials: TeachingMaterials | None = None) -> str:
        if self._runtime is None or self._app is None:
            raise RuntimeError("Agent engine is not started")
        message = HumanMessage(content=content, id=turn_id)
        await self._runtime.submit_user_turn(
            context, message, learning_context, learning_progress, exercise_state,
            teaching_materials,
        )
        config = {"configurable": {
            "thread_id": context.session_id,
            "user_id": context.user_id,
            "workspace_id": context.workspace_id,
            "channel": context.channel,
        }}
        state = await self._app.aget_state(config)
        state_messages = state.values.get("messages", [])
        from server.agent.session_storage import record_transcript

        await record_transcript(
            context.session_id,
            public_transcript_messages(state_messages),
            user_id=context.user_id,
            workspace_id=context.workspace_id,
        )
        for item in reversed(state_messages):
            if isinstance(item, AIMessage) and item.content:
                return sanitize_public_output(item.content)
        return ""

    async def inject(self, context: SessionContext, content: str) -> str | None:
        if self._runtime is None:
            raise RuntimeError("Agent engine is not started")
        return await self._runtime.inject_user_message(
            context,
            HumanMessage(content=content, id=str(uuid.uuid4())),
        )

    async def cancel_turn(self, context: SessionContext, turn_id: str) -> None:
        global_task_manager.cancel_turn(context.session_id, turn_id, reason="gateway_cancelled")

    async def delete_session(self, context: SessionContext) -> None:
        self._session_model_profiles.pop(context.storage_key, None)
        if self._runtime is not None:
            await self._runtime.release_session(context.session_id)
        if self._app is not None:
            checkpointer = getattr(self._app, "checkpointer", None)
            if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
                await checkpointer.adelete_thread(
                    context.session_id,
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                )

    async def _apply_pending_snips(self, context: SessionContext) -> None:
        if self._app is None:
            return
        from server.agent.compression.snip_compact import snip_by_id_range

        config = {"configurable": {
            "thread_id": context.session_id,
            "user_id": context.user_id,
            "workspace_id": context.workspace_id,
            "channel": context.channel,
        }}
        state = await self._app.aget_state(config)
        messages = state.values.get("messages", [])
        for message in reversed(messages):
            if not getattr(message, "tool_calls", None):
                continue
            if message.additional_kwargs.get("_snip_applied"):
                continue
            for tool_call in message.tool_calls:
                if tool_call.get("name") != "SnipTool":
                    continue
                args = tool_call.get("args", {})
                if args.get("to_id"):
                    result = snip_by_id_range(
                        messages, to_id=args["to_id"], from_id=args.get("from_id")
                    )
                    if result.tokens_freed:
                        await self._app.aupdate_state(config, {"messages": result.messages})
                message.additional_kwargs["_snip_applied"] = True
                return

    async def close(self) -> None:
        if not self._started:
            return
        if self._runtime is not None:
            await self._runtime.close()
        from core.observability.runtime import global_telemetry
        from core.tool_registry import physical_tool_manager
        from server.agent.session_storage import global_session_storage
        from server.memory.runtime import global_memory_runtime

        await global_session_storage.close()
        await global_memory_runtime.close()
        await physical_tool_manager.close()
        await global_telemetry.close()
        if self._connection is not None:
            if hasattr(self._connection, "aclose"):
                await self._connection.aclose()
            elif hasattr(self._connection, "close"):
                await self._connection.close()
        self._runtime = None
        self._app = None
        self._connection = None
        self._started = False


def _is_internal_model_event(metadata: dict) -> bool:
    """Keep compaction/model-maintenance streams out of the public chat."""
    return bool(
        metadata.get("compression_internal") is True
        or metadata.get("model_role") in {"compression", "utility_compact"}
        or metadata.get("usage_purpose") == "compact"
    )
