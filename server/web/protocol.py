"""Mapping from framework-neutral Gateway events to the public WebUI protocol."""

from __future__ import annotations

from typing import Any

from gateway.contracts import GatewayEvent, GatewayEventType
from server.web.contracts import ServerEventEnvelope


_EVENT_TYPES: dict[GatewayEventType, str] = {
    GatewayEventType.TURN_ACCEPTED: "chat.accepted",
    GatewayEventType.TURN_STARTED: "chat.started",
    GatewayEventType.TURN_HANDOVER: "worker.progress",
    GatewayEventType.TURN_COMPLETED: "chat.completed",
    GatewayEventType.TURN_FAILED: "chat.error",
    GatewayEventType.TURN_CANCELLED: "chat.cancelled",
    GatewayEventType.MESSAGE_DELTA: "chat.delta",
    GatewayEventType.MESSAGE_COMPLETED: "chat.message.completed",
    GatewayEventType.MESSAGE_INJECTED: "chat.injected",
    GatewayEventType.TOOL_STARTED: "tool.started",
    GatewayEventType.TOOL_COMPLETED: "tool.completed",
    GatewayEventType.TOOL_FAILED: "tool.error",
    GatewayEventType.WORKER_UPDATE: "worker.progress",
    GatewayEventType.GAP: "stream.gap",
}


def _public_event_type(event: GatewayEvent) -> str:
    if event.type == GatewayEventType.MESSAGE_DELTA and event.payload.get("channel") == "reasoning":
        return "chat.reasoning.delta"
    if event.type == GatewayEventType.WORKER_UPDATE:
        status = str(event.payload.get("status", "")).lower()
        phase = str(event.payload.get("phase", "")).lower()
        if status in {"started", "running"} or phase == "started":
            return "worker.started"
        if status in {"completed", "succeeded"} or phase == "completed":
            return "worker.completed"
        if status in {"failed", "error", "cancelled"} or phase in {"failed", "error"}:
            return "worker.error"
    return _EVENT_TYPES[event.type]


def gateway_event_envelope(event: GatewayEvent) -> ServerEventEnvelope:
    return ServerEventEnvelope(
        type=_public_event_type(event),
        event_id=event.event_id,
        session_id=event.session_id,
        turn_id=event.turn_id,
        sequence=event.sequence,
        timestamp=event.created_at,
        payload=event.payload,
    )


def control_event(
    event_type: str,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ServerEventEnvelope:
    return ServerEventEnvelope(
        type=event_type,
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id,
        payload=payload or {},
    )
