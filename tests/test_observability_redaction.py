import tempfile
import uuid
from pathlib import Path

from core.observability.redaction import redact_telemetry_mapping
from core.observability.context import TelemetryContext
from core.observability.models import SpanKind, SpanRecord, SpanStatus, TelemetryEnvelope, TelemetryEvent, TraceRecord
from core.observability.repository import TelemetryRepository
from core.observability.runtime import TelemetryRuntime


def test_observability_redaction_removes_prompt_and_credential_fields_but_keeps_diagnostics():
    payload = redact_telemetry_mapping(
        {
            "chain_id": "workflow-42",
            "provider_model": "gpt-5.4",
            "prompt": "a private user prompt",
            "authorization": "Bearer secret",
            "nested": {"completion": "private answer", "retry": 2},
        }
    )

    assert payload["chain_id"] == "workflow-42"
    assert payload["provider_model"] == "gpt-5.4"
    assert payload["prompt"] == "[redacted]"
    assert payload["authorization"] == "[redacted]"
    assert payload["nested"] == {"completion": "[redacted]", "retry": 2}


def test_observability_redaction_bounds_untrusted_strings():
    payload = redact_telemetry_mapping({"debug_label": "x" * 1000})

    assert len(payload["debug_label"]) <= 256


def test_runtime_applies_redaction_before_emitting_trace_and_event_envelopes():
    runtime = object.__new__(TelemetryRuntime)
    emitted = []
    runtime._emit = emitted.append
    runtime._trace_starts = {}
    runtime._trace_usage = {}
    runtime._trace_ttft = {}
    context = TelemetryContext.create(session_id="session-1", turn_id="turn-1")

    runtime.start_trace(context, attributes={"chain_id": "workflow-1", "prompt": "private"})
    runtime.event("request.debug", payload={"content": "private", "phase": "model"})
    runtime.complete_trace(context, error=RuntimeError("private output"))

    trace = next(item.payload for item in emitted if item.kind == "trace" and item.payload.completed_at is None)
    event = next(item.payload for item in emitted if item.kind == "event")
    completed = next(item.payload for item in emitted if item.kind == "trace" and item.payload.completed_at is not None)
    assert trace.attributes["prompt"] == "[redacted]"
    assert event.payload["content"] == "[redacted]"
    assert trace.chain_id == "workflow-1"
    assert completed.error_message is None


def test_sqlite_repository_redacts_directly_constructed_envelopes_before_persisting():
    database_path = Path(tempfile.gettempdir()) / f"nlp-agent-redaction-{uuid.uuid4().hex}.sqlite3"
    repository = TelemetryRepository(database_path)
    trace = TraceRecord(
        trace_id="trace-1",
        request_id="request-1",
        session_id="session-1",
        turn_id="turn-1",
        error_message="private exception details",
        attributes={"prompt": "private prompt", "safe": "diagnostic"},
    )
    span = SpanRecord(
        trace_id="trace-1",
        span_id="span-1",
        session_id="session-1",
        turn_id="turn-1",
        kind=SpanKind.MODEL,
        name="model.call",
        status=SpanStatus.ERROR,
        error_message="private provider response",
        attributes={"authorization": "Bearer secret"},
    )
    event = TelemetryEvent(
        event_id="event-1",
        name="request.debug",
        trace_id="trace-1",
        payload={"content": "private output", "phase": "model"},
    )

    repository.write_batch(
        [
            TelemetryEnvelope(kind="trace", payload=trace),
            TelemetryEnvelope(kind="span", payload=span),
            TelemetryEnvelope(kind="event", payload=event),
        ]
    )

    stored_trace = repository._conn.execute(
        "SELECT error_message, attributes_json FROM traces WHERE trace_id=?",
        ("trace-1",),
    ).fetchone()
    stored_span = repository._conn.execute(
        "SELECT error_message, attributes_json FROM spans WHERE span_id=?",
        ("span-1",),
    ).fetchone()
    stored_event = repository._conn.execute(
        "SELECT payload_json FROM events WHERE event_id=?",
        ("event-1",),
    ).fetchone()

    assert stored_trace[0] is None
    assert '"prompt":"[redacted]"' in stored_trace[1]
    assert stored_span[0] is None
    assert '"authorization":"[redacted]"' in stored_span[1]
    assert '"content":"[redacted]"' in stored_event[0]
    assert repository.list_traces(workspace_ids=frozenset()) == []
    repository.close()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database_path}{suffix}")
        if path.exists():
            path.unlink()
