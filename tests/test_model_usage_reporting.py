"""Unit tests for ModelUsageReporter integration, streaming/non-streaming lifecycle, retries, failover, and structured outputs."""

import asyncio
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from pydantic import BaseModel

import core.model_runtime.runtime as model_runtime
from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    GenerationConfig,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    RetryPolicy,
    ThinkingConfig,
    TimeoutPolicy,
)
from core.model_runtime.reporters import InMemoryModelUsageReporter, ModelUsageReporterSlot
from core.model_runtime.runtime import (
    EmptyModelResponseError,
    ModelCandidate,
    ModelRuntimeExhaustedError,
    ResilientChatModel,
    StreamInterruptedError,
)
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    MissingUsageAttributionError,
    UsageReporterUnavailableError,
    UsageAttributionContext,
    bind_usage_attribution,
)
from core.observability.context import TelemetryContext, bind_telemetry_context
from core.observability.models import SpanStatus


def _preset(*, attempts: int = 1) -> ModelPresetConfig:
    return ModelPresetConfig(
        model="test-model",
        thinking=ThinkingConfig(enabled=False, effort="none"),
        generation=GenerationConfig(max_output_tokens=100),
        timeouts=TimeoutPolicy(connect_s=1, first_token_s=1, stream_idle_s=1, total_s=2),
        retry=RetryPolicy(max_attempts=attempts, base_delay_s=0, max_delay_s=0, jitter="none"),
        circuit_breaker=CircuitBreakerPolicy(failure_threshold=10, cooldown_s=1),
    )


def _definition(model_id: str = "test-model", provider: str = "test-prov") -> ModelDefinition:
    return ModelDefinition(
        provider=provider,
        model_id=model_id,
        pricing_key=f"{provider}/{model_id}",
        context_window_tokens=1000,
        max_output_tokens=100,
        capabilities=ModelCapabilities(thinking=True, structured_output=True),
    )


def _candidate(name: str, model: object, *, attempts: int = 1, provider: str = "test-prov") -> ModelCandidate:
    return ModelCandidate(
        preset_name=name,
        provider_name=provider,
        model_name=name,
        definition=_definition(name, provider=provider),
        preset=_preset(attempts=attempts),
        model=model,
    )


def _sample_attribution() -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="req-1",
        user_id="user-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        purpose="coordinator",
    )


class FakeStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str = "error"):
        super().__init__(message)
        self.status_code = status_code


class FakeRawModel:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _input: object, config: object = None, **_kwargs: object) -> object:
        self.calls += 1
        if not self.responses:
            raise EmptyModelResponseError("no response")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def astream(self, input: object, config: object = None, **kwargs: object):
        resp = await self.ainvoke(input, config=config, **kwargs)
        if isinstance(resp, list):
            for chunk in resp:
                yield chunk
        else:
            yield resp

    def bind_tools(self, _tools: list[object], **_kwargs: object):
        return self

    def with_structured_output(self, schema: object, **kwargs: object):
        return FakeStructuredModel(self.responses, schema=schema, include_raw=kwargs.get("include_raw", False))


class FakeStructuredModel(FakeRawModel):
    def __init__(self, responses: list[object], schema: object, include_raw: bool = False):
        super().__init__(responses)
        self.schema = schema
        self.include_raw = include_raw

    async def ainvoke(self, input: object, config: object = None, **kwargs: object) -> object:
        raw_or_parsed = await super().ainvoke(input, config=config, **kwargs)
        if isinstance(raw_or_parsed, dict) and "raw" in raw_or_parsed:
            return raw_or_parsed
        # wrap into raw dict
        raw_msg = AIMessage(
            content="parsed",
            response_metadata={"id": "resp-struct-1", "token_usage": {"prompt_tokens": 40, "completion_tokens": 10}},
        )
        return {
            "raw": raw_msg,
            "parsed": raw_or_parsed,
            "parsing_error": None,
        }


@pytest.mark.asyncio
async def test_non_streaming_success_reports_once():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    msg = AIMessage(
        content="hello world",
        response_metadata={"id": "resp-1", "token_usage": {"prompt_tokens": 50, "completion_tokens": 20}},
    )
    fake = FakeRawModel([[AIMessageChunk(content="hello world", response_metadata={"id": "resp-1", "token_usage": {"prompt_tokens": 50, "completion_tokens": 20}})]])
    cand = _candidate("test-cand", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot, model_profile="test-profile")

    with bind_usage_attribution(_sample_attribution()):
        result = await resilient.ainvoke([HumanMessage(content="hi")])

    assert isinstance(result, AIMessage)
    assert result.content == "hello world"
    assert len(reporter.events) == 1
    inv, usage, outcome = reporter.events[0]
    assert outcome.status == "succeeded"
    assert usage.input_tokens == 50
    assert usage.output_tokens == 20
    assert usage.total_tokens == 70
    assert usage.provider_response_id == "resp-1"
    assert inv.identity.model_profile == "test-profile"
    assert inv.attempt == 1
    assert inv.fallback_index == 0


@pytest.mark.asyncio
async def test_streaming_success_reports_once():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    chunks = [
        AIMessageChunk(content="Hello "),
        AIMessageChunk(
            content="world!",
            response_metadata={"id": "stream-resp-1", "token_usage": {"prompt_tokens": 60, "completion_tokens": 30}},
        ),
    ]
    fake = FakeRawModel([chunks])
    cand = _candidate("stream-cand", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        collected = []
        async for chunk in resilient.astream([HumanMessage(content="hi")]):
            collected.append(chunk.content)

    assert "".join(collected) == "Hello world!"
    assert len(reporter.events) == 1
    inv, usage, outcome = reporter.events[0]
    assert outcome.status == "succeeded"
    assert usage.input_tokens == 60
    assert usage.output_tokens == 30
    assert usage.provider_response_id == "stream-resp-1"


@pytest.mark.asyncio
async def test_empty_stream_retry_then_success():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    # First attempt: empty list of chunks (EmptyModelResponseError)
    # Second attempt: valid chunks
    success_chunk = AIMessageChunk(
        content="retry success",
        response_metadata={"id": "resp-retry-2", "token_usage": {"prompt_tokens": 20, "completion_tokens": 10}},
    )
    fake = FakeRawModel([[], [success_chunk]])
    cand = _candidate("retry-cand", fake, attempts=2)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        collected = []
        async for chunk in resilient.astream([HumanMessage(content="hi")]):
            collected.append(chunk.content)

    assert "".join(collected) == "retry success"
    assert len(reporter.events) == 2
    inv1, usage1, outcome1 = reporter.events[0]
    assert outcome1.status == "failed"
    assert outcome1.error_kind == "upstream_empty_response"
    assert inv1.attempt == 1

    inv2, usage2, outcome2 = reporter.events[1]
    assert outcome2.status == "succeeded"
    assert inv2.attempt == 2
    assert inv1.operation_id != inv2.operation_id


@pytest.mark.asyncio
async def test_overloaded_fallback_success():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    fake1 = FakeRawModel([FakeStatusError(503, "Service Unavailable"), FakeStatusError(503, "Service Unavailable")])
    fake2 = FakeRawModel([[AIMessageChunk(
        content="fallback ok",
        response_metadata={"id": "resp-fb-1", "token_usage": {"prompt_tokens": 15, "completion_tokens": 5}},
    )]])

    cand1 = _candidate("primary-cand", fake1, attempts=2, provider="prov-a")
    cand2 = _candidate("fallback-cand", fake2, attempts=2, provider="prov-b")
    resilient = ResilientChatModel([cand1, cand2], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        collected = []
        async for chunk in resilient.astream([HumanMessage(content="hi")]):
            collected.append(chunk.content)

    assert "".join(collected) == "fallback ok"
    assert len(reporter.events) == 3

    # Attempt 1 on primary
    assert reporter.events[0][0].attempt == 1
    assert reporter.events[0][0].fallback_index == 0
    assert reporter.events[0][2].status == "failed"
    assert reporter.events[0][2].error_kind == "upstream_overloaded"

    # Attempt 2 on primary
    assert reporter.events[1][0].attempt == 2
    assert reporter.events[1][0].fallback_index == 0
    assert reporter.events[1][2].status == "failed"

    # Attempt 1 on fallback candidate
    assert reporter.events[2][0].attempt == 1
    assert reporter.events[2][0].fallback_index == 1
    assert reporter.events[2][0].identity.provider == "prov-b"
    assert reporter.events[2][2].status == "succeeded"


@pytest.mark.asyncio
async def test_non_retryable_400_invalid_request():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    fake = FakeRawModel([FakeStatusError(400, "Bad Request: invalid prompt")])
    cand = _candidate("cand-400", fake, attempts=3)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(FakeStatusError):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert len(reporter.events) == 1
    inv, usage, outcome = reporter.events[0]
    assert outcome.status == "failed"
    assert outcome.error_kind == "upstream_invalid_request"
    assert inv.attempt == 1


@pytest.mark.asyncio
async def test_failed_provider_attempt_reports_usage_from_error_body():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)
    error = FakeStatusError(400, "Bad Request")
    error.body = {
        "id": "failed-response-1",
        "usage": {"prompt_tokens": 17, "completion_tokens": 2},
    }
    fake = FakeRawModel([error])
    resilient = ResilientChatModel(
        [_candidate("cand-error-usage", fake)], reporter_slot=slot
    )

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(FakeStatusError):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert len(reporter.events) == 1
    _, usage, outcome = reporter.events[0]
    assert outcome.status == "failed"
    assert usage.source == "provider"
    assert usage.input_tokens == 17
    assert usage.output_tokens == 2
    assert usage.provider_response_id == "failed-response-1"


def test_http_402_is_provider_quota_exhausted():
    from core.model_runtime.runtime import classify_model_error

    decision = classify_model_error(FakeStatusError(402, "Payment Required"))

    assert decision.kind == "upstream_provider_quota_exhausted"
    assert decision.retryable is False


@pytest.mark.asyncio
async def test_stream_interrupted_after_visible_output():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    async def _failing_stream(_input, **_kwargs):
        yield AIMessageChunk(content="Partial visible text")
        raise ConnectionResetError("Connection lost mid-stream")

    fake = FakeRawModel([])
    fake.astream = _failing_stream
    cand = _candidate("cand-stream-err", fake, attempts=3)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(StreamInterruptedError):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert len(reporter.events) == 1
    inv, usage, outcome = reporter.events[0]
    assert outcome.status == "interrupted"
    assert outcome.error_kind == "upstream_connection_error"
    assert usage.semantics == "partial"


@pytest.mark.asyncio
async def test_stream_delta_usage_is_aggregated_and_finalized_once():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    async def _delta_stream(_input, **_kwargs):
        yield AIMessageChunk(
            content="part one",
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 3,
                    "usage_semantics": "delta",
                }
            },
        )
        yield AIMessageChunk(
            content="part two",
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "usage_semantics": "delta",
                }
            },
        )

    fake = FakeRawModel([])
    fake.astream = _delta_stream
    cand = _candidate("cand-delta-stream", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        chunks = [chunk async for chunk in resilient.astream([HumanMessage(content="hi")])]

    assert len(chunks) == 2
    assert len(reporter.events) == 1
    _, usage, outcome = reporter.events[0]
    assert usage.input_tokens == 9
    assert usage.output_tokens == 5
    assert usage.total_tokens == 14
    assert usage.semantics == "final"
    assert outcome.status == "succeeded"


@pytest.mark.asyncio
async def test_cancelled_stream_reports_cancelled():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    async def _cancelled_stream(_input, **_kwargs):
        raise asyncio.CancelledError()
        yield AIMessageChunk(content="never")

    fake = FakeRawModel([])
    fake.astream = _cancelled_stream
    cand = _candidate("cand-cancel", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(asyncio.CancelledError):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert len(reporter.events) == 1
    assert reporter.events[0][2].status == "cancelled"


@pytest.mark.asyncio
async def test_reporter_failure_halts_retries():
    class BrokenReporter:
        async def report(self, invocation, usage, outcome):
            raise RuntimeError("Database connection down")

    slot = ModelUsageReporterSlot(BrokenReporter())
    fake = FakeRawModel([FakeStatusError(500), FakeStatusError(500)])
    cand = _candidate("cand-broken", fake, attempts=3)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(RuntimeError, match="Database connection down"):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert fake.calls == 1  # Did not continue to attempt 2


@pytest.mark.asyncio
async def test_reporter_failure_after_success_does_not_replay_provider():
    class BrokenReporter:
        def __init__(self) -> None:
            self.calls = 0

        async def report(self, invocation, usage, outcome):
            self.calls += 1
            raise RuntimeError("Database connection down")

    reporter = BrokenReporter()
    slot = ModelUsageReporterSlot(reporter)
    fake = FakeRawModel([
        [AIMessageChunk(
            content="completed",
            response_metadata={
                "id": "success-before-reporter-failure",
                "token_usage": {"prompt_tokens": 9, "completion_tokens": 3},
            },
        )],
        [AIMessageChunk(content="must not be requested")],
    ])
    cand = _candidate("cand-success-broken-reporter", fake, attempts=3)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(RuntimeError, match="Database connection down"):
            async for _ in resilient.astream([HumanMessage(content="hi")]):
                pass

    assert fake.calls == 1
    assert reporter.calls == 1


@pytest.mark.asyncio
async def test_structured_output_success_and_parse_error():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    class SampleOut(BaseModel):
        ans: str

    fake = FakeRawModel([
        {"raw": AIMessage(content="ok", response_metadata={"id": "s-1", "token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}), "parsed": SampleOut(ans="good"), "parsing_error": None},
        {"raw": AIMessage(content="bad", response_metadata={"id": "s-2", "token_usage": {"prompt_tokens": 12, "completion_tokens": 6}}), "parsed": None, "parsing_error": ValueError("Malformed JSON")},
    ])
    cand = _candidate("cand-struct", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot)
    structured_model = resilient.with_structured_output(SampleOut)

    with bind_usage_attribution(_sample_attribution()):
        # 1. Success
        res1 = await structured_model.ainvoke([HumanMessage(content="give answer")])
        assert isinstance(res1, SampleOut)
        assert res1.ans == "good"

        # 2. Parse Error
        with pytest.raises(ValueError, match="Malformed JSON"):
            await structured_model.ainvoke([HumanMessage(content="give bad answer")])

    assert len(reporter.events) == 2
    assert reporter.events[0][2].status == "succeeded"
    assert reporter.events[0][1].input_tokens == 10

    assert reporter.events[1][2].status == "failed"
    assert reporter.events[1][1].input_tokens == 12
    assert reporter.events[1][2].error_kind == "structured_output_parse_error"


@pytest.mark.asyncio
async def test_structured_output_updates_telemetry_before_span_closes(
    monkeypatch,
):
    class RecordingSpan:
        def __init__(self) -> None:
            self.active = False
            self.attributes = {}
            self.usage = None
            self.status = None
            self.error_kind = None

        async def __aenter__(self):
            self.active = True
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            self.active = False
            return False

        def set_usage(self, usage):
            assert self.active, "usage was recorded after the span closed"
            self.usage = usage

        def annotate(self, **attributes):
            assert self.active, "attributes were recorded after the span closed"
            self.attributes.update(attributes)

        def set_status(self, status, *, error_kind=None, error_message=None):
            assert self.active, "status was recorded after the span closed"
            self.status = status
            self.error_kind = error_kind

    class RecordingTelemetry:
        def __init__(self) -> None:
            self.spans = []

        def span(self, *_args, **_kwargs):
            span = RecordingSpan()
            self.spans.append(span)
            return span

        def event(self, *_args, **_kwargs):
            pass

    class SampleOut(BaseModel):
        ans: str

    raw = AIMessage(
        content="bad",
        response_metadata={
            "id": "span-structured-1",
            "finish_reason": "stop",
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 2},
        },
    )
    fake = FakeRawModel(
        [
            {
                "raw": raw,
                "parsed": None,
                "parsing_error": ValueError("Malformed JSON"),
            }
        ]
    )
    telemetry = RecordingTelemetry()
    monkeypatch.setattr(model_runtime, "global_telemetry", telemetry)
    structured_model = ResilientChatModel(
        [_candidate("cand-span-struct", fake)]
    ).with_structured_output(SampleOut)
    telemetry_context = TelemetryContext.create(
        session_id="session-structured",
        turn_id="turn-structured",
    )

    with bind_usage_attribution(_sample_attribution()):
        with bind_telemetry_context(telemetry_context):
            with pytest.raises(ValueError, match="Malformed JSON"):
                await structured_model.ainvoke(
                    [HumanMessage(content="give bad answer")]
                )

    assert len(telemetry.spans) == 1
    span = telemetry.spans[0]
    assert span.active is False
    assert span.usage["total_tokens"] == 9
    assert span.attributes == {
        "structured_output": True,
        "finish_reason": "stop",
    }
    assert span.status == SpanStatus.ERROR
    assert span.error_kind == "structured_output_parse_error"


@pytest.mark.asyncio
async def test_missing_attribution_raises_before_calling_provider():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)
    fake = FakeRawModel([AIMessage(content="hi")])
    cand = _candidate("cand-no-attr", fake)
    resilient = ResilientChatModel([cand], reporter_slot=slot)

    # No attribution or telemetry context bound
    with pytest.raises(MissingUsageAttributionError):
        await resilient.ainvoke([HumanMessage(content="hi")])

    assert fake.calls == 0


@pytest.mark.asyncio
async def test_required_reporter_fails_before_calling_provider():
    slot = ModelUsageReporterSlot(required=True)
    fake = FakeRawModel([AIMessage(content="must not run")])
    resilient = ResilientChatModel([_candidate("cand-no-reporter", fake)], reporter_slot=slot)

    with bind_usage_attribution(_sample_attribution()):
        with pytest.raises(UsageReporterUnavailableError, match="requires"):
            await resilient.ainvoke([HumanMessage(content="hi")])

    assert fake.calls == 0


@pytest.mark.asyncio
async def test_call_site_attribution_purposes():
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)

    from core.observability.context import TelemetryContext, bind_telemetry_context
    from server.agent.compression import auto_compact, context_collapse
    from server.memory.curator import MemoryCurator
    from server.tools.vision.vlm import ModelRuntimeVLMProvider
    from server.tools.vision.contracts import ImageAsset, ImageReference, ImageLanguage
    from evaluation.exercise_blueprint.student_simulator import FlashExerciseStudentSimulator
    from evaluation.exercise_blueprint.models import ExerciseStudentProfile, ExerciseBlueprintFixture
    from evaluation.exercise_blueprint.judge import judge_case

    # 1. Telemetry Context without worker_id -> purpose="coordinator"
    telem_coord = TelemetryContext.create(
        session_id="s-1",
        turn_id="t-1",
        user_id="u-1",
        workspace_id="ws-1",
    )
    fake_coord = FakeRawModel([[AIMessageChunk(content="coordinator reply")]])
    model_coord = ResilientChatModel([_candidate("coord-c", fake_coord)], reporter_slot=slot)
    with bind_telemetry_context(telem_coord):
        await model_coord.ainvoke([HumanMessage(content="hi")])
    assert reporter.events[-1][0].attribution.purpose == "coordinator"
    assert reporter.events[-1][0].attribution.worker_id is None

    # 2. Telemetry Context with worker_id -> purpose="worker"
    telem_worker = telem_coord.child(worker_id="worker-w1")
    fake_worker = FakeRawModel([[AIMessageChunk(content="worker reply")]])
    model_worker = ResilientChatModel([_candidate("worker-c", fake_worker)], reporter_slot=slot)
    with bind_telemetry_context(telem_worker):
        await model_worker.ainvoke([HumanMessage(content="hi")])
    assert reporter.events[-1][0].attribution.purpose == "worker"
    assert reporter.events[-1][0].attribution.worker_id == "worker-w1"

    # 3. Compact: auto_compact._generate_global_summary -> purpose="compact"
    class FakeFactory:
        def __init__(self, model):
            self.model = model
            self.reporter_slot = slot
        def build_profile_role(self, *a, **kw):
            return self.model
        def build_route(self, *a, **kw):
            return self.model
        def build_preset(self, *a, **kw):
            return self.model
        def profile_preset(self, *a, **kw):
            return "utility-flash"

    fake_compact = FakeRawModel([[AIMessageChunk(content="summary text")]])
    model_compact = ResilientChatModel([_candidate("compact-c", fake_compact)], reporter_slot=slot)
    monkeypatch_factory = FakeFactory(model_compact)

    import server.agent.llm_factory as server_llm_factory
    import core.prompt_runtime as pr

    orig_get_factory = server_llm_factory.get_global_model_factory
    server_llm_factory.get_global_model_factory = lambda: monkeypatch_factory
    try:
        with bind_telemetry_context(telem_coord):
            await auto_compact._generate_global_summary([HumanMessage(content="dialogue msg")])
        assert reporter.events[-1][0].attribution.purpose == "compact"

        # 4. Memory: MemoryCurator.curate -> purpose="memory"
        class FakeMemoryManager:
            def get_curator_cursor(self):
                return 0
            def read_archives(self, since_cursor=0):
                return []

        curator = MemoryCurator()
        from core.session_context import SessionContext
        session_ctx = SessionContext(session_id="s-1", workspace_id="ws-1", user_id="u-1")
        with bind_telemetry_context(telem_coord):
            await curator.curate(session_ctx, FakeMemoryManager())

        # 5. Evaluation: FlashExerciseStudentSimulator -> purpose="evaluation", user_id="system"
        fake_sim = FakeRawModel([[AIMessageChunk(content="student answer")]])
        model_sim = ResilientChatModel([_candidate("sim-c", fake_sim)], reporter_slot=slot)
        # This test exercises the attribution wrapper in ``answer``. Avoid the
        # production constructor because it builds a real Provider model and
        # therefore requires an API key that CI deliberately does not expose.
        simulator = FlashExerciseStudentSimulator.__new__(
            FlashExerciseStudentSimulator
        )
        simulator.model = model_sim
        profile = ExerciseStudentProfile(id="prof-1", role="student", behavior_rules=["polite"])
        blueprint = ExerciseBlueprintFixture(
            id="bp-1",
            name="name",
            topic_id="top-1",
            topic_name="Top",
            knowledge_point_id="kp-1",
            knowledge_point_name="KP",
            knowledge_markdown="content",
            instructions="inst",
            question_type="concept",
            rubric=[{"point": "p1"}],
        )
        await simulator.answer(profile=profile, blueprint=blueprint, question="what is tf?")
        assert reporter.events[-1][0].attribution.purpose == "evaluation"
        assert reporter.events[-1][0].attribution.user_id == "system"

        # 6. Evaluation: judge_case -> purpose="evaluation", user_id="system"
        fake_judge_model = FakeRawModel([[AIMessageChunk(content='{"overall": 2}')]])
        judge_resilient = ResilientChatModel([_candidate("judge-c", fake_judge_model)], reporter_slot=slot)
        outcome_mock = {
            "case_id": "c-1",
            "snapshot": {"turns": [{"question": "q"}, {"student_reply": "ans", "agent_reply": "good"}]},
        }
        await judge_case(judge_resilient, blueprint={}, outcome=outcome_mock)
        assert reporter.events[-1][0].attribution.purpose == "evaluation"
        assert reporter.events[-1][0].attribution.user_id == "system"
    finally:
        server_llm_factory.get_global_model_factory = orig_get_factory
