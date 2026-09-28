"""Non-blocking telemetry runtime with span helpers and live subscribers."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any

from configs.settings import settings
from core.observability.context import (
    TelemetryContext,
    bind_telemetry_context,
    current_telemetry_context,
)
from core.observability.models import (
    SpanKind,
    SpanRecord,
    SpanStatus,
    TelemetryEnvelope,
    TelemetryEvent,
    TokenUsage,
    TraceRecord,
)
from core.observability.redaction import redact_telemetry_mapping
from core.observability.traces import derive_trace_identity
from core.observability.mysql_repository import MySQLTelemetryRepository
from core.observability.repository import TelemetryRepository
from utils.logger import get_logger


logger = get_logger("nlp_agent.telemetry")


def usage_from_metadata(metadata: dict[str, Any] | None) -> TokenUsage:
    data = metadata or {}
    input_tokens = int(data.get("input_tokens", data.get("prompt_tokens", 0)) or 0)
    output_tokens = int(data.get("output_tokens", data.get("completion_tokens", 0)) or 0)
    input_token_details = data.get("input_token_details") or {}
    cached_tokens = data.get("prompt_cache_hit_tokens")
    if cached_tokens is None:
        cached_tokens = data.get("cached_tokens")
    if cached_tokens is None:
        cached_tokens = input_token_details.get("cache_read", 0)
    cached_tokens = int(cached_tokens or 0)
    cache_miss_tokens = int(data.get("prompt_cache_miss_tokens", input_token_details.get("cache_miss", 0)) or 0)
    reasoning_tokens = int(data.get("reasoning_tokens", data.get("output_token_details", {}).get("reasoning", 0)) or 0)
    total = int(data.get("total_tokens", input_tokens + output_tokens) or 0)
    return TokenUsage(
        input_tokens=max(0, input_tokens), output_tokens=max(0, output_tokens),
        cached_tokens=max(0, cached_tokens), total_tokens=max(0, total),
        cache_miss_tokens=max(0, cache_miss_tokens),
        reasoning_tokens=max(0, reasoning_tokens),
        source="provider" if data else "none",
    )


class Span(AbstractAsyncContextManager["Span"]):
    def __init__(self, runtime: "TelemetryRuntime", context: TelemetryContext, *,
                 kind: SpanKind, name: str, parent_span_id: str | None,
                 worker_id: str | None = None, attempt: int = 1,
                 attributes: dict[str, Any] | None = None) -> None:
        self.runtime = runtime
        self.context = context
        self.kind = kind
        self.name = name
        self.parent_span_id = parent_span_id
        self.worker_id = worker_id
        self.attempt = attempt
        self.attributes = redact_telemetry_mapping(attributes)
        self.started_at = datetime.now(timezone.utc)
        self._started_perf = time.perf_counter()
        self._binding = None
        self.usage = TokenUsage()
        self.status = SpanStatus.RUNNING
        self.ttft_ms: int | None = None
        self.error_kind: str | None = None
        self.error_message: str | None = None

    async def __aenter__(self) -> "Span":
        self._binding = bind_telemetry_context(self.context)
        self._binding.__enter__()
        self.runtime.emit_span(self._record())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if exc is not None:
            self.status = SpanStatus.CANCELLED if isinstance(exc, asyncio.CancelledError) else SpanStatus.ERROR
            self.error_kind = type(exc).__name__
            self.error_message = None
        elif self.status == SpanStatus.RUNNING:
            self.status = SpanStatus.OK
        self.runtime.emit_span(self._record(completed=True))
        if self._binding is not None:
            self._binding.__exit__(exc_type, exc, traceback)
        return False

    def set_usage(self, metadata: dict[str, Any] | TokenUsage | None) -> None:
        self.usage = metadata if isinstance(metadata, TokenUsage) else usage_from_metadata(metadata)

    def set_status(self, status: SpanStatus, *, error_kind: str | None = None,
                   error_message: str | None = None) -> None:
        self.status = status
        self.error_kind = error_kind
        # Error kinds and status are useful operational signals.  Provider and
        # application messages may contain prompts, outputs, credentials or
        # personal data, so they never cross the telemetry boundary.
        self.error_message = None

    def annotate(self, **attributes: Any) -> None:
        sanitized = redact_telemetry_mapping(attributes)
        if "ttft_ms" in sanitized:
            try:
                value = int(sanitized["ttft_ms"])
            except (TypeError, ValueError):
                value = -1
            if value >= 0:
                self.ttft_ms = value
        self.attributes.update(sanitized)

    def _record(self, completed: bool = False) -> SpanRecord:
        return SpanRecord(
            trace_id=self.context.trace_id, span_id=self.context.span_id,
            parent_span_id=self.parent_span_id, session_id=self.context.session_id,
            turn_id=self.context.turn_id, worker_id=self.worker_id or self.context.worker_id,
            kind=self.kind, name=self.name, started_at=self.started_at,
            completed_at=datetime.now(timezone.utc) if completed else None,
            duration_ms=max(0, int((time.perf_counter() - self._started_perf) * 1000)) if completed else None,
            ttft_ms=self.ttft_ms,
            status=self.status, attempt=self.attempt, usage=self.usage,
            error_kind=self.error_kind, error_message=self.error_message,
            attributes=self.attributes,
        )


class TelemetryRuntime:
    def __init__(self, path: str | None = None, *, queue_size: int = 5000,
                 batch_size: int = 100, flush_interval_s: float = .25) -> None:
        if path is not None:
            # An explicit path is an opt-in isolated store for tests and local
            # tools. It must never construct or clear the process-wide MySQL
            # repository.
            self.repository = TelemetryRepository(path)
        else:
            database_url = settings.NLP_AGENT_DATABASE_URL.strip()
            if not database_url:
                raise RuntimeError("NLP_AGENT_DATABASE_URL is required for MySQL observability")
            self.repository = MySQLTelemetryRepository(database_url)
        self.queue_size = queue_size
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self._queue: asyncio.Queue[TelemetryEnvelope] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.dropped_events = 0
        self._trace_starts: dict[str, tuple[TraceRecord, float]] = {}
        self._trace_usage: dict[str, TokenUsage] = {}
        self._trace_ttft: dict[str, int] = {}

    def _ensure_started(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not None and self._loop is not loop:
            # Embedded hosts and test lifespans may replace the event loop. Move
            # any rows left behind by the old writer synchronously before a new
            # loop-owned queue is created, otherwise Queue.join() can wait forever.
            pending: list[TelemetryEnvelope] = []
            if self._queue is not None:
                while True:
                    try:
                        pending.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            if pending:
                self.repository.write_batch(pending)
            self._queue = None
            self._writer = None
        self._loop = loop
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self.queue_size)
        if self._writer is None or self._writer.done():
            self._writer = loop.create_task(self._writer_loop(), name="telemetry-writer")

    def _emit(self, envelope: TelemetryEnvelope) -> None:
        self._ensure_started()
        if self._queue is None:
            self.repository.write_batch([envelope])
            return
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped_events += 1
            if envelope.kind != "event" or getattr(envelope.payload, "level", "debug") != "debug":
                logger.warning("Telemetry queue full", dropped_events=self.dropped_events)
            return
        live = envelope.model_dump(mode="json")
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.put_nowait(live)
            except asyncio.QueueFull:
                pass

    def emit_span(self, span: SpanRecord) -> None:
        if span.completed_at is not None and span.kind == SpanKind.MODEL:
            previous = self._trace_usage.get(span.trace_id, TokenUsage())
            source = "provider" if "provider" in {previous.source, span.usage.source} else (
                "estimated" if "estimated" in {previous.source, span.usage.source} else "none"
            )
            self._trace_usage[span.trace_id] = TokenUsage(
                input_tokens=previous.input_tokens + span.usage.input_tokens,
                output_tokens=previous.output_tokens + span.usage.output_tokens,
                cached_tokens=previous.cached_tokens + span.usage.cached_tokens,
                cache_miss_tokens=previous.cache_miss_tokens + span.usage.cache_miss_tokens,
                reasoning_tokens=previous.reasoning_tokens + span.usage.reasoning_tokens,
                total_tokens=previous.total_tokens + span.usage.total_tokens,
                source=source,
            )
        self._emit(TelemetryEnvelope(kind="span", payload=span))

    def event(self, name: str, *, level: str = "info", payload: dict[str, Any] | None = None,
              context: TelemetryContext | None = None) -> None:
        ctx = context or current_telemetry_context()
        event = TelemetryEvent(
            event_id=uuid.uuid4().hex, level=level, name=name,
            trace_id=ctx.trace_id if ctx else None, span_id=ctx.span_id if ctx else None,
            session_id=ctx.session_id if ctx else None, turn_id=ctx.turn_id if ctx else None,
            worker_id=ctx.worker_id if ctx else None,
            payload=redact_telemetry_mapping(payload),
        )
        self._emit(TelemetryEnvelope(kind="event", payload=event))

    def start_trace(self, context: TelemetryContext, *, source: str = "user",
                    attributes: dict[str, Any] | None = None) -> None:
        trace_attributes = redact_telemetry_mapping(attributes)
        identity = derive_trace_identity(
            session_id=context.session_id,
            turn_id=context.turn_id,
            source=source,
            channel=context.channel,
            attributes=trace_attributes,
        )
        record = TraceRecord(
            trace_id=context.trace_id, request_id=context.request_id,
            session_id=context.session_id, turn_id=context.turn_id,
            chain_id=identity["chain_id"], chain_name=identity["chain_name"],
            entrypoint=identity["entrypoint"],
            workspace_id=context.workspace_id, user_id=context.user_id,
            channel=context.channel, source=source, attributes=trace_attributes,
        )
        self._trace_starts[context.trace_id] = (record, time.perf_counter())
        self._trace_usage[context.trace_id] = TokenUsage()
        self._emit(TelemetryEnvelope(kind="trace", payload=record))

    def mark_ttft(self, context: TelemetryContext | None = None) -> None:
        ctx = context or current_telemetry_context()
        if ctx is None or ctx.trace_id in self._trace_ttft:
            return
        item = self._trace_starts.get(ctx.trace_id)
        if item is not None:
            self._trace_ttft[ctx.trace_id] = max(0, int((time.perf_counter() - item[1]) * 1000))

    def complete_trace(self, context: TelemetryContext, *, status: SpanStatus = SpanStatus.OK,
                       usage: TokenUsage | None = None, ttft_ms: int | None = None,
                       error: BaseException | None = None,
                       attributes: dict[str, Any] | None = None) -> None:
        original, started = self._trace_starts.pop(context.trace_id, (
            TraceRecord(trace_id=context.trace_id, request_id=context.request_id,
                        session_id=context.session_id, turn_id=context.turn_id,
                        workspace_id=context.workspace_id, user_id=context.user_id,
                        channel=context.channel), time.perf_counter()))
        record = original.model_copy(update={
            "completed_at": datetime.now(timezone.utc),
            "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "ttft_ms": ttft_ms if ttft_ms is not None else self._trace_ttft.pop(context.trace_id, None),
            "status": status, "usage": usage or self._trace_usage.pop(context.trace_id, original.usage),
            "error_kind": type(error).__name__ if error else None,
            "error_message": None,
            "attributes": {
                **original.attributes,
                **redact_telemetry_mapping(attributes),
            },
        })
        self._emit(TelemetryEnvelope(kind="trace", payload=record))

    def span(self, kind: SpanKind | str, name: str, *,
             context: TelemetryContext | None = None, worker_id: str | None = None,
             attempt: int = 1, attributes: dict[str, Any] | None = None) -> Span:
        parent = context or current_telemetry_context()
        if parent is None:
            raise RuntimeError("A TelemetryContext is required to create a span")
        child = parent.child(worker_id=worker_id)
        return Span(self, child, kind=SpanKind(kind), name=name,
                    parent_span_id=parent.span_id, worker_id=worker_id,
                    attempt=attempt, attributes=attributes)

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _writer_loop(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            batch: list[TelemetryEnvelope] = []
            try:
                first = await queue.get()
                batch.append(first)
                deadline = asyncio.get_running_loop().time() + self.flush_interval_s
                while len(batch) < self.batch_size:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(queue.get(), remaining))
                    except asyncio.TimeoutError:
                        break
                await asyncio.to_thread(self.repository.write_batch, batch)
            except asyncio.CancelledError:
                # A host loop can stop without calling close(). Persist the
                # already-dequeued batch so loop replacement cannot lose it.
                if batch:
                    self.repository.write_batch(batch)
                raise
            finally:
                for _ in batch:
                    queue.task_done()

    async def flush(self) -> None:
        self._ensure_started()
        if self._queue is not None:
            await self._queue.join()

    async def close(self) -> None:
        await self.flush()
        if self._writer is not None:
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)
            self._writer = None
        self._loop = None
        self.repository.close()

    def health(self) -> dict[str, Any]:
        return {
            **self.repository.health(), "queue_size": self._queue.qsize() if self._queue else 0,
            "queue_capacity": self.queue_size, "dropped_events": self.dropped_events,
            "live_subscribers": len(self._subscribers),
            "active_traces": len(self._trace_starts),
        }


global_telemetry = TelemetryRuntime()
