from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol

import httpx

from evaluation.core.judge import ToolRoutingJudge
from evaluation.core.models import EvaluationCase, EvaluationReport, TurnEvidence, ToolCallEvidence, WorkerEvidence, DispatchEvidence


class EvaluationTurnTimeout(TimeoutError):
    """A submitted evaluation turn exceeded its deadline and was cancelled."""

    def __init__(self, turn_id: str) -> None:
        super().__init__(f"Evaluation turn timed out: {turn_id}")
        self.turn_id = turn_id


class CaseExecutor(Protocol):
    async def start(self) -> None: ...
    async def run(self, case: EvaluationCase, *, workspace_id: str, timeout_s: float, evaluation_run_id: str, suite_id: str) -> tuple[str, str | None]: ...
    async def close(self) -> None: ...


class EvidenceReader(Protocol):
    async def read(self, turn_id: str) -> TurnEvidence: ...


def _evidence_from_trace(trace: dict, detail: dict | None, turn_id: str) -> TurnEvidence:
    spans = detail["spans"] if detail else []
    calls = []
    worker_spans = [
        item for item in spans
        if item.get("kind") == "worker" and item.get("worker_id") and item.get("name") == "worker.attempt"
    ]
    for sequence, span in enumerate(
        sorted(
            (item for item in spans if item.get("kind") == "tool"),
            key=lambda item: (item.get("started_at", ""), item.get("span_id", "")),
        ),
        start=1,
    ):
        attributes = span.get("attributes") or {}
        tool_name = str(
            attributes.get("tool_name")
            or str(span.get("name", "")).removeprefix("tool.")
        )
        calls.append(
            ToolCallEvidence(
                trace_id=trace["trace_id"], turn_id=turn_id, tool_name=tool_name,
                sequence=sequence, status=str(span.get("status")),
                attempts=int(attributes.get("attempts", span.get("attempt", 1)) or 1),
                argument_keys=tuple(attributes.get("argument_keys", [])),
                duration_ms=int(span.get("duration_ms") or 0),
                worker_id=span.get("worker_id"),
                result_urls=tuple(attributes.get("result_urls", [])),
            )
        )
    worker_attempts = [
        WorkerEvidence(
            worker_id=str(worker_span["worker_id"]),
            attempt=int(worker_span.get("attempt", 1) or 1),
            status=str(worker_span.get("status")),
            duration_ms=int(worker_span.get("duration_ms") or 0),
            started_at=worker_span.get("started_at"),
            completed_at=worker_span.get("completed_at"),
        )
        for worker_span in sorted(worker_spans, key=lambda item: (item.get("started_at", ""), item.get("span_id", "")))
    ]
    workers = []
    for worker_id in dict.fromkeys(attempt.worker_id for attempt in worker_attempts):
        attempts = [attempt for attempt in worker_attempts if attempt.worker_id == worker_id]
        latest_attempt = attempts[-1]
        workers.append(WorkerEvidence(
            worker_id=worker_id,
            attempt=latest_attempt.attempt,
            status=latest_attempt.status,
            duration_ms=sum(attempt.duration_ms for attempt in attempts),
            tool_names=tuple(call.tool_name for call in calls if call.worker_id == worker_id),
            started_at=attempts[0].started_at,
            completed_at=latest_attempt.completed_at,
        ))
    dispatches = []
    for event in detail.get("events", []) if detail else []:
        if event.get("name") != "agent.worker.dispatched":
            continue
        payload = event.get("payload") or {}
        dispatches.append(DispatchEvidence(
            worker_id=str(payload.get("worker_id", "")), agent_name=str(payload.get("agent_name", "")),
            join=bool(payload.get("join")), wait_mode=str(payload.get("wait_mode", "")),
            quorum=int(payload.get("quorum", 0)), directive_chars=int(payload.get("directive_chars", 0)),
        ))
    return TurnEvidence(
        trace_id=trace["trace_id"], turn_id=turn_id,
        trace_status=trace.get("status"), calls=tuple(calls), workers=tuple(workers),
        worker_attempts=tuple(worker_attempts), dispatches=tuple(dispatches),
    )


class RemoteApiExecutor:
    """Runs cases through the already-running Web Gateway, never a second Gateway."""

    def __init__(
        self,
        web_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.web_url = web_url.rstrip("/")
        self.username = (username or "").strip() or None
        self._password = password or None
        self.client: httpx.AsyncClient | None = None
        self._write_headers: dict[str, str] = {}

    async def start(self) -> None:
        if bool(self.username) != bool(self._password):
            raise RuntimeError(
                "both NLP_AGENT_EVALUATION_USERNAME and "
                "NLP_AGENT_EVALUATION_PASSWORD are required"
            )
        self.client = httpx.AsyncClient(base_url=self.web_url, timeout=20)
        if self.username:
            try:
                response = await self.client.post(
                    "/api/v1/auth/login",
                    headers={"Origin": self.web_url},
                    json={"username": self.username, "password": self._password},
                )
            finally:
                # Do not retain the clear-text secret for the duration of a long
                # evaluation run. The authenticated cookie is sufficient.
                self._password = None
        else:
            response = await self.client.post(
                "/api/v1/auth/guest", headers={"Origin": self.web_url}
            )
            if response.status_code in {401, 403, 404, 405}:
                # Compatibility with older explicitly injected local adapters.
                response = await self.client.post(
                    "/api/v1/auth/session", headers={"Origin": self.web_url}
                )
        response.raise_for_status()
        self._write_headers = {"Origin": self.web_url, "X-CSRF-Token": response.json()["csrf_token"]}

    def session_cookies(self) -> httpx.Cookies:
        if self.client is None:
            raise RuntimeError("executor is not started")
        return httpx.Cookies(self.client.cookies)

    async def run(self, case: EvaluationCase, *, workspace_id: str, timeout_s: float, evaluation_run_id: str, suite_id: str) -> tuple[str, str | None]:
        if self.client is None:
            raise RuntimeError("executor is not started")
        session = await self.client.post("/api/v1/sessions", json={"workspace_id": workspace_id}, headers=self._write_headers)
        session.raise_for_status()
        accepted = await self.client.post("/api/v1/chat/turns", json={"session_id": session.json()["session_id"], "content": case.input, "idempotency_key": f"evaluation-{uuid.uuid4().hex}", "evaluation": {"run_id": evaluation_run_id, "suite_id": suite_id, "case_id": case.id}}, headers=self._write_headers)
        accepted.raise_for_status()
        turn_id = accepted.json()["turn_id"]
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    turn = await self.client.get(f"/api/v1/chat/turns/{turn_id}")
                    turn.raise_for_status()
                    payload = turn.json()
                    if payload["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                        return turn_id, payload.get("final_text")
                    await asyncio.sleep(0.1)
        except TimeoutError as error:
            cancelled = await self.client.post(
                f"/api/v1/chat/turns/{turn_id}/cancel", headers=self._write_headers
            )
            cancelled.raise_for_status()
            raise EvaluationTurnTimeout(turn_id) from error

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None


class MonitorHttpEvidenceReader:
    def __init__(
        self,
        monitor_url: str,
        *,
        timeout_s: float = 10,
        session_cookie_provider: Callable[[], httpx.Cookies] | None = None,
    ) -> None:
        self.monitor_url = monitor_url.rstrip("/")
        self.timeout_s = timeout_s
        self.session_cookie_provider = session_cookie_provider
        self.client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None:
            cookies = (
                self.session_cookie_provider()
                if self.session_cookie_provider is not None
                else None
            )
            self.client = httpx.AsyncClient(
                base_url=self.monitor_url, timeout=20, cookies=cookies
            )
            if cookies is not None:
                response = await self.client.get(
                    "/api/v1/auth/session", headers={"Origin": self.monitor_url}
                )
            else:
                response = await self.client.post(
                    "/api/v1/auth/session", headers={"Origin": self.monitor_url}
                )
            response.raise_for_status()
        return self.client

    async def read(self, turn_id: str) -> TurnEvidence:
        client = await self._ensure_client()
        async with asyncio.timeout(self.timeout_s):
            while True:
                traces = await client.get("/api/v1/observability/traces", params={"limit": 500})
                traces.raise_for_status()
                trace = next((item for item in traces.json()["items"] if item.get("turn_id") == turn_id and item.get("status") != "running"), None)
                if trace is not None:
                    detail = await client.get(f"/api/v1/observability/traces/{trace['trace_id']}")
                    detail.raise_for_status()
                    return _evidence_from_trace(trace, detail.json(), turn_id)
                await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None


class EvaluationRunner:
    def __init__(self, executor: CaseExecutor, evidence: EvidenceReader, judge: ToolRoutingJudge | None = None) -> None:
        self.executor = executor
        self.evidence = evidence
        self.judge = judge or ToolRoutingJudge()

    async def run(self, *, suite_id: str, dataset_sha256: str, cases: list[EvaluationCase], workspace_id: str, timeout_s: float, on_case: Callable[[str], Awaitable[None]] | None = None) -> EvaluationReport:
        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)
        results = []
        await self.executor.start()
        try:
            for case in cases:
                if on_case is not None:
                    await on_case(case.id)
                try:
                    turn_id, final_text = await self.executor.run(case, workspace_id=workspace_id, timeout_s=timeout_s, evaluation_run_id=run_id, suite_id=suite_id)
                    evidence = await self.evidence.read(turn_id)
                    results.append(self.judge.judge(case, evidence, final_text=final_text))
                except EvaluationTurnTimeout as error:
                    evidence = await self.evidence.read(error.turn_id)
                    results.append(self.judge.judge(case, evidence, final_text=str(error)))
                except (TimeoutError, asyncio.TimeoutError) as error:
                    results.append(self.judge.judge(case, TurnEvidence(turn_id="timeout"), final_text=str(error)))
        finally:
            await self.executor.close()
            close_evidence = getattr(self.evidence, "close", None)
            if close_evidence is not None:
                await close_evidence()
        completed_at = datetime.now(timezone.utc)
        eligible = [result for result in results if result.verdict != "BLOCKED"]
        passed = [result for result in eligible if result.verdict == "PASS"]
        critical = [result for result in results if "critical" in next(case.tags for case in cases if case.id == result.case_id)]
        metrics = {
            "case_pass_rate": len(passed) / len(eligible) if eligible else 0.0,
            "critical_pass_rate": sum(result.verdict == "PASS" for result in critical) / len(critical) if critical else 1.0,
            "trace_capture_rate": len(eligible) / len(results) if results else 0.0,
            "macro_tool_f1": sum((2 * result.metrics.get("tool_precision", 0) * result.metrics.get("tool_recall", 0) / (result.metrics.get("tool_precision", 0) + result.metrics.get("tool_recall", 0)) if result.metrics.get("tool_precision", 0) + result.metrics.get("tool_recall", 0) else 0) for result in eligible) / len(eligible) if eligible else 0.0,
        }
        academic_positive = [
            result
            for result in eligible
            if "academic_search"
            in next(case.expectation.required_tools for case in cases if case.id == result.case_id)
        ]
        academic_negative = [
            result
            for result in eligible
            if next(case.expectation.expected_no_tool for case in cases if case.id == result.case_id)
        ]
        if academic_positive or academic_negative:
            metrics.update(
                {
                    "academic_search_recall": (
                        sum(
                            any(call.tool_name == "academic_search" for call in result.tool_calls)
                            for result in academic_positive
                        )
                        / len(academic_positive)
                        if academic_positive
                        else 1.0
                    ),
                    "academic_false_positive_rate": (
                        sum(
                            any(call.tool_name == "academic_search" for call in result.tool_calls)
                            for result in academic_negative
                        )
                        / len(academic_negative)
                        if academic_negative
                        else 0.0
                    ),
                    "citation_integrity_rate": (
                        sum(result.metrics.get("citation_integrity", 1.0) for result in academic_positive)
                        / len(academic_positive)
                        if academic_positive
                        else 1.0
                    ),
                }
            )
        verdict = "BLOCKED" if not eligible else "PASS" if metrics["critical_pass_rate"] == 1 and metrics["case_pass_rate"] >= .95 and metrics["macro_tool_f1"] >= .90 else "FAIL"
        return EvaluationReport(run_id=run_id, suite_id=suite_id, dataset_sha256=dataset_sha256, started_at=started_at, completed_at=completed_at, results=tuple(results), metrics=metrics, verdict=verdict)
