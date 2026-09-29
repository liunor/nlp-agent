from gateway.contracts import GatewayEvent, GatewayEventType
from server.web.protocol import gateway_event_envelope


def test_handover_event_is_exposed_as_worker_recovery_progress() -> None:
    event = GatewayEvent(
        event_id="event-handover",
        turn_id="turn-1",
        session_id="session-1",
        sequence=8,
        type=GatewayEventType.TURN_HANDOVER,
        payload={"reason": "lease_expired"},
    )

    envelope = gateway_event_envelope(event)

    assert envelope.type == "worker.progress"
    assert envelope.payload == {"reason": "lease_expired"}
