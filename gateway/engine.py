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
from core.worker_events import global_worker_event_bus
from gateway.contracts import GatewayEventType
import json
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from .contracts import ChatRequest
from security.content_guard import get_content_guard
from security.prompt_guard import get_prompt_guard
from security.audit import get_audit_logger
from security.auth import get_identity_manager
import asyncio
from security.tool_guard import get_tool_guard, Verdict

EngineEventSink = Callable[
    [str, str, GatewayEventType, dict], Awaitable[None]
]


class AgentEngine(Protocol):
    async def start(self, event_sink: EngineEventSink) -> None: ...
    async def run_turn(self, context: SessionContext, turn_id: str, content: str, *, learning_context: LearningContext | None = None, learning_progress: LearningProgress | None = None, exercise_state: ExerciseState | None = None, teaching_materials: TeachingMaterials | None = None) -> str: ...
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
        config = {
            "recursion_limit": 64,
            "configurable": {
                "thread_id": context.session_id,
                "turn_id": turn_id,
                "user_id": context.user_id,
                "workspace_id": context.workspace_id,
                "channel": context.channel,
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
        async for event in self._app.astream_events(
            {"messages": messages}, config=config, version="v2"
        ):
            metadata = event.get("metadata", {})
            node = metadata.get("langgraph_node")
            event_name = event.get("event")
            if event_name == "on_chat_model_stream" and node == "coordinator":
                chunk = event.get("data", {}).get("chunk")
                if not isinstance(chunk, AIMessageChunk):
                    continue
                if chunk.content:
                    await self._emit(
                        turn_id,
                        context.session_id,
                        GatewayEventType.MESSAGE_DELTA,
                        {"delta": chunk.content, "background": background},
                    )
                reasoning = chunk.additional_kwargs.get("reasoning_content")
                if reasoning:
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

                tool_guard = get_tool_guard()
                verdict, reason = tool_guard.evaluate(tool_name, {})  # 不传 args 粗略检查
                if verdict == Verdict.BLOCK:
                    # 记录并取消任务
                    get_audit_logger().log_security_event("tool_blocked", {
                        "session": context.session_id,
                        "tool": tool_name,
                        "reason": reason
                    })
                    # 取消当前 turn
                    global_task_manager.cancel_turn(context.session_id, turn_id, reason="tool_blocked")
                    # 发送错误事件
                    await self._emit(turn_id, context.session_id, GatewayEventType.TOOL_FAILED,
                                     {"name": tool_name, "error": "工具被安全策略阻止"})
                    return

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
        await self._apply_pending_snips(context.session_id)

    async def run_turn(self, context: SessionContext, turn_id: str, content: str, *, learning_context: LearningContext | None = None, learning_progress: LearningProgress | None = None, exercise_state: ExerciseState | None = None, teaching_materials: TeachingMaterials | None = None) -> str:
        if self._runtime is None or self._app is None:
            raise RuntimeError("Agent engine is not started")
        message = HumanMessage(content=content, id=turn_id)
        await self._runtime.submit_user_turn(
            context, message, learning_context, learning_progress, exercise_state,
            teaching_materials,
        )
        config = {"configurable": {"thread_id": context.session_id}}
        state = await self._app.aget_state(config)
        state_messages = state.values.get("messages", [])
        from server.agent.session_storage import record_transcript

        await record_transcript(context.session_id, state_messages)
        for item in reversed(state_messages):
            if isinstance(item, AIMessage) and item.content:
                return str(item.content)
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
        if self._runtime is not None:
            await self._runtime.release_session(context.session_id)
        if self._app is not None:
            checkpointer = getattr(self._app, "checkpointer", None)
            if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
                await checkpointer.adelete_thread(context.session_id)

    async def _apply_pending_snips(self, session_id: str) -> None:
        if self._app is None:
            return
        from server.agent.compression.snip_compact import snip_by_id_range

        config = {"configurable": {"thread_id": session_id}}
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
            await self._connection.close()
        self._runtime = None
        self._app = None
        self._connection = None
        self._started = False


class GatewayEngine:
    def __init__(self, agent_engine: LangGraphAgentEngine):
        self.content_guard = get_content_guard()
        self.prompt_guard = get_prompt_guard()
        self.audit = get_audit_logger()
        self.identity_manager = get_identity_manager()

        # 持有 Agent 引擎
        self.agent_engine = agent_engine

        # 管理 WebSocket 连接：session_id -> WebSocket
        self._websockets: dict[str, WebSocket] = {}
    # HTTP 请求入口（非流式）
    async def handle_http_request(self, request: Request):
        body = await request.json()
        chat_req = ChatRequest(**body)
        user_input = chat_req.prompt
        session_id = chat_req.session_id or "anonymous"

        # 输入安检（与上面相同）
        is_safe, reason, _ = self.content_guard.validate_input(user_input)
        if not is_safe:
            self.audit.log_security_event("content_block",
                                          {"session": session_id, "input": user_input[:50], "reason": reason})
            return JSONResponse(status_code=400,
                                content={"error": "内容违规", "code": "CONTENT_BLOCK", "detail": reason})
        is_safe, threat, _ = self.prompt_guard.scan_user_input(user_input)
        if not is_safe:
            self.audit.log_security_event("prompt_injection", {"session": session_id, "threat": threat})
            return JSONResponse(status_code=400, content={"error": "检测到注入攻击", "code": "INJECTION_DETECTED"})

        # 调用模型
        context = SessionContext(session_id=session_id, user_id="http_user", workspace_id="default", channel="http")
        turn_id = str(uuid.uuid4())
        # 注意：run_turn 会返回最终回复（字符串）
        reply = await self.agent_engine.run_turn(
            context, turn_id, user_input,
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=None
        )
        # 输出审核
        is_safe, reason, _ = self.content_guard.validate_output(reply)
        if not is_safe:
            self.audit.log_security_event("output_filtered", {"session": session_id, "reason": reason})
            return JSONResponse(
                status_code=400,
                content={"error": "生成的回复违规", "code": "OUTPUT_BLOCKED"}
            )
        return JSONResponse(content={"reply": reply})
    # WebSocket 入口（流式）
    async def handle_websocket(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        # 注册连接
        self._websockets[session_id] = websocket
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    user_input = msg.get("prompt") or msg.get("message")
                    if not user_input:
                        continue
                    # ---- 输入安检 ----
                    is_safe, reason, _ = self.content_guard.validate_input(user_input)
                    if not is_safe:
                        await websocket.send_text(json.dumps({
                            "error": "内容违规", "code": "CONTENT_BLOCK", "detail": reason
                        }))
                        continue
                    is_safe, threat, _ = self.prompt_guard.scan_user_input(user_input)
                    if not is_safe:
                        await websocket.send_text(json.dumps({
                            "error": "检测到注入攻击", "code": "INJECTION_DETECTED"
                        }))
                        continue
                    # ---- 调用模型 ----
                    # 构造 SessionContext
                    context = SessionContext(
                        session_id=session_id,
                        user_id=msg.get("user_id", "anonymous"),
                        workspace_id=msg.get("workspace_id", "default"),
                        channel="websocket"
                    )
                    turn_id = str(uuid.uuid4())
                    # 异步执行，不等待完成，事件会通过 _event_sink 推送
                    asyncio.create_task(
                        self.agent_engine.run_turn(
                            context, turn_id, user_input,
                            learning_context=None,
                            learning_progress=None,
                            exercise_state=None,
                            teaching_materials=None
                        )
                    )
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"error": "无效的 JSON"}))
        except WebSocketDisconnect:
            pass
        finally:
            # 清理连接
            self._websockets.pop(session_id, None)
    async def _event_sink(self, turn_id: str, session_id: str, event_type: GatewayEventType, payload: dict):
        """事件分发器：将 Agent 事件发送给对应的 WebSocket（如果存在）"""
        # 1. 如果是消息增量，进行输出审核
        if event_type == GatewayEventType.MESSAGE_DELTA:
            delta = payload.get("delta", "")
            if delta:
                is_safe, reason, _ = self.content_guard.validate_output(delta)
                if not is_safe:
                    # 记录违规并取消当前 turn
                    self.audit.log_security_event("output_filtered_stream",
                        {"session": session_id, "turn": turn_id, "reason": reason})
                    # 发送安全警告给客户端
                    ws = self._websockets.get(session_id)
                    if ws:
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "code": "OUTPUT_BLOCKED",
                            "message": "生成内容违规，已中断"
                        }))
                    # 取消当前任务
                    await self.agent_engine.cancel_turn(SessionContext(session_id=session_id), turn_id)
                    return
        # 2. 转发事件到对应的 WebSocket
        ws = self._websockets.get(session_id)
        if ws:
            try:
                await ws.send_text(json.dumps({
                    "turn_id": turn_id,
                    "event": event_type.value,
                    "payload": payload
                }))
            except Exception as e:
                # 发送失败，可能连接已断开
                pass