from __future__ import annotations

import asyncio
import httpx
from evaluation.core.judge import ToolRoutingJudge
from evaluation.core.models import EvaluationCase, ToolCallEvidence, TurnEvidence, WorkerEvidence
from evaluation.core.runner import EvaluationRunner, EvaluationTurnTimeout, RemoteApiExecutor, _evidence_from_trace
from evaluation.core.runner import MonitorHttpEvidenceReader
from evaluation.core.reporting import TraceMetrics, render_report
from evaluation.core.models import CaseResult, EvaluationReport
from evaluation.generate_result_report import render_markdown
from datetime import datetime, timezone

import pytest


def _case(*, tags: list[str] | None = None, expectation: dict | None = None) -> EvaluationCase:
    return EvaluationCase(
        id="case", input="question", tags=tags or [], expectation=expectation or {}
    )


def _evidence(*tools: str) -> TurnEvidence:
    return TurnEvidence(
        trace_id="trace", turn_id="turn", trace_status="ok",
        calls=tuple(
            ToolCallEvidence(trace_id="trace", turn_id="turn", tool_name=name, sequence=index, status="ok")
            for index, name in enumerate(tools, start=1)
        ),
    )


def test_tool_routing_judge_accepts_required_tool_and_rejects_extra_or_missing():
    judge = ToolRoutingJudge()
    expected = _case(expectation={"required_tools": ["nlp_bleu_score"]})

    assert judge.judge(expected, _evidence("nlp_bleu_score")).verdict == "PASS"
    extra = judge.judge(expected, _evidence("nlp_bleu_score", "nlp_ngram_analyzer"))
    assert extra.verdict == "FAIL"
    assert extra.hard_failures == ("unexpected_called:nlp_ngram_analyzer",)
    assert judge.judge(expected, _evidence()).verdict == "FAIL"


def test_tool_routing_score_is_bounded_when_a_required_tool_is_repeated():
    result = ToolRoutingJudge().judge(
        _case(expectation={"required_tools": ["nlp_bleu_score"]}),
        _evidence("nlp_bleu_score", "nlp_bleu_score"),
    )

    assert result.metrics["status_score"] == 1
    assert 0 <= result.score <= 100


def test_tool_routing_metrics_exclude_orchestration_tools_from_business_tool_quality():
    result = ToolRoutingJudge().judge(
        _case(expectation={"required_tools": ["nlp_bleu_score"], "allow_extra_tools": True}),
        _evidence("spawn_worker", "nlp_bleu_score"),
    )

    assert result.verdict == "PASS"
    assert result.metrics["tool_precision"] == 1
    assert result.metrics["efficiency"] == 1


def test_tool_routing_judge_handles_order_and_no_tool_cases():
    judge = ToolRoutingJudge()
    ordered = _case(expectation={"required_tools": ["first", "second"], "ordered_tools": ["first", "second"]})
    assert judge.judge(ordered, _evidence("first", "second")).verdict == "PASS"
    assert judge.judge(ordered, _evidence("second", "first")).verdict == "FAIL"
    no_tool = _case(expectation={"expected_no_tool": True})
    assert judge.judge(no_tool, _evidence()).verdict == "PASS"
    assert judge.judge(no_tool, _evidence("nlp_bleu_score")).verdict == "FAIL"


def test_academic_citation_integrity_requires_link_evidence_from_tool_result():
    case = _case(
        expectation={
            "required_tools": ["academic_search"],
            "require_citation_integrity": True,
        }
    )
    call = ToolCallEvidence(
        trace_id="trace",
        turn_id="turn",
        tool_name="academic_search",
        sequence=1,
        status="ok",
        result_urls=("https://arxiv.org/abs/1706.03762",),
    )
    evidence = TurnEvidence(
        trace_id="trace", turn_id="turn", trace_status="ok", calls=(call,)
    )

    valid = ToolRoutingJudge().judge(
        case,
        evidence,
        final_text="论文链接：https://arxiv.org/abs/1706.03762",
    )
    invalid = ToolRoutingJudge().judge(
        case,
        evidence,
        final_text="论文链接：https://arxiv.org/abs/9999.99999",
    )

    assert valid.verdict == "PASS"
    assert valid.metrics["citation_integrity"] == 1
    assert invalid.verdict == "FAIL"
    assert "citation_not_in_tool_result" in invalid.hard_failures
    assert invalid.metrics["citation_integrity"] == 0


def test_tool_routing_judge_requires_worker_allocation_and_worker_owned_tools():
    case = _case(expectation={"required_tools": ["nlp_bleu_score"], "min_workers": 1, "worker_required_tools": ["nlp_bleu_score"]})
    evidence = TurnEvidence(trace_id="trace", turn_id="turn", trace_status="ok", calls=(ToolCallEvidence(trace_id="trace", turn_id="turn", tool_name="nlp_bleu_score", sequence=1, status="ok", worker_id="worker-1"),), workers=(WorkerEvidence(worker_id="worker-1", status="ok", tool_names=("nlp_bleu_score",)),))
    assert ToolRoutingJudge().judge(case, evidence).verdict == "PASS"
    assert "insufficient_workers:0/1" in ToolRoutingJudge().judge(case, _evidence("nlp_bleu_score")).hard_failures


def test_tool_routing_judge_rejects_worker_dispatch_for_a_simple_multi_tool_case():
    case = _case(expectation={"required_tools": ["first"], "max_workers": 0, "max_dispatches": 0})
    evidence = TurnEvidence(trace_id="trace", turn_id="turn", trace_status="ok", calls=(ToolCallEvidence(trace_id="trace", turn_id="turn", tool_name="first", sequence=1, status="ok"),), workers=(WorkerEvidence(worker_id="worker-1", status="ok"),), dispatches=())

    assert "excessive_workers:1/0" in ToolRoutingJudge().judge(case, evidence).hard_failures


def test_tool_routing_judge_allows_coordinator_execution_for_preferred_delegation():
    case = _case(expectation={
        "required_tools": ["nlp_ngram_analyzer", "nlp_bleu_score"],
        "ordered_tools": ["nlp_ngram_analyzer", "nlp_bleu_score"],
        "min_workers": 2,
        "min_dispatches": 2,
        "delegation_policy": "preferred",
    })

    result = ToolRoutingJudge().judge(case, _evidence("nlp_ngram_analyzer", "nlp_bleu_score"))

    assert result.verdict == "PASS"
    assert result.metrics["delegation_preference_met"] == 0


class FakeExecutor:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.evaluation_contexts = []

    async def start(self) -> None:
        self.started = True

    async def run(self, case, *, workspace_id, timeout_s, evaluation_run_id, suite_id):
        self.evaluation_contexts.append((evaluation_run_id, suite_id, case.id))
        return case.id, f"answer:{workspace_id}:{timeout_s}"

    async def close(self) -> None:
        self.closed = True


class FakeEvidenceReader:
    async def read(self, turn_id: str) -> TurnEvidence:
        return TurnEvidence(
            trace_id=f"trace-{turn_id}", turn_id=turn_id, trace_status="ok",
            calls=(ToolCallEvidence(trace_id=f"trace-{turn_id}", turn_id=turn_id, tool_name="nlp_bleu_score", sequence=1, status="ok"),),
        )


async def test_evaluation_runner_aggregates_fake_live_execution_without_model_api():
    executor = FakeExecutor()
    runner = EvaluationRunner(executor, FakeEvidenceReader())
    report = await runner.run(
        suite_id="suite", dataset_sha256="hash",
        cases=[_case(expectation={"required_tools": ["nlp_bleu_score"]})],
        workspace_id="eval", timeout_s=2,
    )

    assert executor.started is True
    assert executor.closed is True
    assert report.verdict == "PASS"
    assert report.metrics["case_pass_rate"] == 1
    assert report.results[0].trace_id == "trace-case"
    assert executor.evaluation_contexts == [(report.run_id, "suite", "case")]


async def test_evaluation_runner_reports_academic_recall_false_positives_and_citations():
    positive = EvaluationCase(
        id="academic-positive",
        input="find paper",
        tags=["academic"],
        expectation={
            "required_tools": ["academic_search"],
            "require_citation_integrity": True,
        },
    )
    negative = EvaluationCase(
        id="academic-negative",
        input="write code",
        tags=["negative"],
        expectation={"expected_no_tool": True, "forbidden_tools": ["academic_search"]},
    )

    class AcademicEvidenceReader:
        async def read(self, turn_id: str) -> TurnEvidence:
            calls = ()
            if turn_id == "academic-positive":
                calls = (
                    ToolCallEvidence(
                        trace_id="trace-positive",
                        turn_id=turn_id,
                        tool_name="academic_search",
                        sequence=1,
                        status="ok",
                        result_urls=("https://arxiv.org/abs/1706.03762",),
                    ),
                )
            return TurnEvidence(
                trace_id=f"trace-{turn_id}",
                turn_id=turn_id,
                trace_status="ok",
                calls=calls,
            )

    report = await EvaluationRunner(
        FakeExecutor(), AcademicEvidenceReader()
    ).run(
        suite_id="academic-routing-v1",
        dataset_sha256="hash",
        cases=[positive, negative],
        workspace_id="eval",
        timeout_s=2,
    )

    assert report.metrics["academic_search_recall"] == 1
    assert report.metrics["academic_false_positive_rate"] == 0
    assert report.metrics["citation_integrity_rate"] == 1


class TimedOutExecutor(FakeExecutor):
    async def run(self, *_args, **_kwargs):
        raise EvaluationTurnTimeout("timed-out-turn")


async def test_evaluation_runner_reads_trace_after_a_known_turn_timeout():
    executor = TimedOutExecutor()
    runner = EvaluationRunner(executor, FakeEvidenceReader())
    report = await runner.run(
        suite_id="suite", dataset_sha256="hash",
        cases=[_case(expectation={"required_tools": ["nlp_bleu_score"]})],
        workspace_id="eval", timeout_s=2,
    )

    assert report.results[0].trace_id == "trace-timed-out-turn"
    assert report.results[0].verdict == "PASS"


@pytest.mark.asyncio
async def test_remote_executor_cancels_submitted_turn_when_case_deadline_expires():
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class Client:
        def __init__(self):
            self.cancelled = []

        async def post(self, path, **_kwargs):
            if path == "/api/v1/sessions":
                return Response({"session_id": "session"})
            if path == "/api/v1/chat/turns":
                return Response({"turn_id": "turn"})
            self.cancelled.append(path)
            return Response({"status": "cancelled"})

        async def get(self, _path):
            await asyncio.sleep(0.05)
            return Response({"status": "running"})

    executor = RemoteApiExecutor("http://testserver")
    client = Client()
    executor.client = client
    executor._write_headers = {"Origin": "http://testserver", "X-CSRF-Token": "csrf"}

    with pytest.raises(EvaluationTurnTimeout, match="turn"):
        await executor.run(_case(), workspace_id="eval", timeout_s=0.01, evaluation_run_id="run", suite_id="suite")

    assert client.cancelled == ["/api/v1/chat/turns/turn/cancel"]


@pytest.mark.asyncio
async def test_remote_executor_uses_production_login_without_retaining_password(
    monkeypatch,
):
    class Client:
        def __init__(self):
            self.cookies = httpx.Cookies({"nlp_session": "signed-session"})
            self.calls = []

        async def post(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return httpx.Response(
                200,
                json={"csrf_token": "csrf"},
                request=httpx.Request("POST", f"http://testserver{path}"),
            )

        async def aclose(self):
            return None

    client = Client()
    monkeypatch.setattr(
        "evaluation.core.runner.httpx.AsyncClient", lambda **_kwargs: client
    )
    executor = RemoteApiExecutor(
        "http://testserver", username="evaluation-user", password="secret"
    )

    await executor.start()

    assert client.calls == [
        (
            "/api/v1/auth/login",
            {
                "headers": {"Origin": "http://testserver"},
                "json": {
                    "username": "evaluation-user",
                    "password": "secret",
                },
            },
        )
    ]
    assert executor._password is None
    assert executor._write_headers["X-CSRF-Token"] == "csrf"
    assert executor.session_cookies().get("nlp_session") == "signed-session"


@pytest.mark.asyncio
async def test_monitor_reader_reuses_web_session_cookie(monkeypatch):
    captured = {}

    class Client:
        async def get(self, path, **kwargs):
            captured["request"] = (path, kwargs)
            return httpx.Response(
                200,
                json={"csrf_token": "rotated"},
                request=httpx.Request("GET", f"http://monitor{path}"),
            )

        async def aclose(self):
            return None

    def client_factory(**kwargs):
        captured["cookies"] = kwargs["cookies"]
        return Client()

    monkeypatch.setattr("evaluation.core.runner.httpx.AsyncClient", client_factory)
    reader = MonitorHttpEvidenceReader(
        "http://monitor",
        session_cookie_provider=lambda: httpx.Cookies(
            {"nlp_session": "signed-session"}
        ),
    )

    await reader._ensure_client()

    assert captured["cookies"].get("nlp_session") == "signed-session"
    assert captured["request"] == (
        "/api/v1/auth/session",
        {"headers": {"Origin": "http://monitor"}},
    )


def test_monitor_trace_evidence_uses_tool_spans_not_gateway_tool_node_events():
    evidence = _evidence_from_trace(
        {"trace_id": "trace", "status": "ok"},
        {
            "spans": [
                {"kind": "tool", "name": "tool.nlp_bleu_score", "span_id": "b", "started_at": "2026-01-01T00:00:02Z", "status": "ok", "attributes": {"tool_name": "nlp_bleu_score", "argument_keys": ["candidate"]}},
                {"kind": "tool", "name": "tool.nlp_ngram_analyzer", "span_id": "a", "started_at": "2026-01-01T00:00:01Z", "status": "ok", "attributes": {"tool_name": "nlp_ngram_analyzer", "attempts": 2}},
                {"kind": "worker", "name": "tools", "span_id": "ignored", "started_at": "2026-01-01T00:00:00Z", "status": "ok", "attributes": {}},
            ]
        },
        "turn",
    )

    assert [call.tool_name for call in evidence.calls] == [
        "nlp_ngram_analyzer",
        "nlp_bleu_score",
    ]
    assert evidence.calls[0].attempts == 2
    assert evidence.calls[1].argument_keys == ("candidate",)


def test_monitor_trace_evidence_captures_coordinator_worker_dispatches():
    evidence = _evidence_from_trace(
        {"trace_id": "trace", "status": "ok"},
        {"spans": [{"kind": "worker", "name": "worker.attempt", "worker_id": "worker-a", "status": "ok", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:01Z", "duration_ms": 1000}], "events": [{"name": "agent.worker.dispatched", "payload": {"worker_id": "worker-a", "agent_name": "nlp", "join": True, "wait_mode": "all", "quorum": 1, "directive_chars": 80}}]},
        "turn",
    )

    assert evidence.dispatches[0].worker_id == "worker-a"
    assert evidence.dispatches[0].join is True
    assert evidence.workers[0].duration_ms == 1000


def test_monitor_trace_evidence_preserves_worker_attempts_for_parallel_judgement():
    evidence = _evidence_from_trace(
        {"trace_id": "trace", "status": "ok"},
        {
            "spans": [
                {"kind": "worker", "name": "worker.attempt", "worker_id": "a", "attempt": 1, "status": "ok", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:20Z"},
                {"kind": "worker", "name": "worker.attempt", "worker_id": "b", "attempt": 1, "status": "ok", "started_at": "2026-01-01T00:00:05Z", "completed_at": "2026-01-01T00:00:15Z"},
                {"kind": "worker", "name": "worker.attempt", "worker_id": "a", "attempt": 2, "status": "ok", "started_at": "2026-01-01T00:00:30Z", "completed_at": "2026-01-01T00:00:40Z"},
                {"kind": "tool", "name": "tool.first", "worker_id": "a", "status": "ok", "started_at": "2026-01-01T00:00:06Z", "attributes": {"tool_name": "first"}},
                {"kind": "tool", "name": "tool.second", "worker_id": "b", "status": "ok", "started_at": "2026-01-01T00:00:07Z", "attributes": {"tool_name": "second"}},
            ],
            "events": [],
        },
        "turn",
    )
    case = _case(expectation={
        "required_tools": ["first", "second"],
        "min_workers": 2,
        "orchestration_mode": "parallel",
    })

    assert len(evidence.workers) == 2
    assert len(evidence.worker_attempts) == 3
    assert "parallel_overlap_missing" not in ToolRoutingJudge().judge(case, evidence).hard_failures


def test_terminal_report_includes_global_tool_case_and_monitor_metrics():
    now = datetime.now(timezone.utc)
    report = EvaluationReport(
        run_id="run", suite_id="suite", dataset_sha256="digest", started_at=now, completed_at=now,
        verdict="FAIL", metrics={"case_pass_rate": .5, "critical_pass_rate": 1, "trace_capture_rate": 1, "macro_tool_f1": .8},
        results=(
            CaseResult(case_id="passing", verdict="PASS", score=100, trace_id="one", tool_calls=_evidence("nlp_bleu_score").calls),
            CaseResult(case_id="failing", verdict="FAIL", score=85, trace_id="two", hard_failures=("unexpected_called:extra",)),
        ),
    )
    output = render_report(report, {"one": TraceMetrics("ok", 120, 10, 3, 5, 0, 8, 1, 1)})

    assert "Routing quality" in output
    assert "Tool-level averages" in output
    assert "Case details" in output
    assert "12 ms" not in output
    assert "120 ms" in output
    assert "unexpected_called:extra" in output


def test_terminal_report_shows_worker_tool_allocation():
    now = datetime.now(timezone.utc)
    report = EvaluationReport(run_id="run", suite_id="suite", dataset_sha256="digest", started_at=now, completed_at=now, verdict="PASS", metrics={}, results=(CaseResult(case_id="case", verdict="PASS", score=100, tool_calls=(ToolCallEvidence(trace_id="trace", turn_id="turn", tool_name="nlp_bleu_score", sequence=1, status="ok", worker_id="worker-a"),)),))

    assert "worker-a:nlp_bleu_score" in render_report(report, {})


def test_markdown_result_report_contains_summary_details_and_objective_failures(tmp_path):
    now = datetime.now(timezone.utc)
    report = EvaluationReport(run_id="run", suite_id="suite", dataset_sha256="digest", started_at=now, completed_at=now, verdict="FAIL", metrics={"case_pass_rate": .5, "critical_pass_rate": .5, "macro_tool_f1": .8, "trace_capture_rate": 1}, results=(CaseResult(case_id="case", verdict="FAIL", score=85, hard_failures=("insufficient_workers:0/2",), trace_id="trace", final_text="answer", tool_calls=(ToolCallEvidence(trace_id="trace", turn_id="turn", tool_name="nlp_bleu_score", sequence=1, status="ok"),)),))
    path = tmp_path / "report.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")

    markdown = render_markdown(path, suite_name="测试", telemetry_db=tmp_path / "missing.sqlite3")

    assert "## 总结" in markdown
    assert "## 逐案例明细" in markdown
    assert "insufficient_workers:0/2" in markdown
