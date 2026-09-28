from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit, urlunsplit

from evaluation.core.models import (
    CaseResult,
    EvaluationCase,
    ToolCallEvidence,
    TurnEvidence,
)


# These calls are runtime orchestration, not user-domain work. They remain in
# validation evidence, but must not lower business tool routing quality.
_ORCHESTRATION_TOOLS = frozenset({"spawn_worker", "send_message", "TaskStop", "SnipTool"})
_ACADEMIC_HOSTS = frozenset(
    {"arxiv.org", "doi.org", "aclanthology.org", "www.semanticscholar.org", "scholar.google.com"}
)
_URL_PATTERN = re.compile(r"https://[^\s<>\])}]+")


def _normalized_academic_urls(text: str) -> set[str]:
    urls: set[str] = set()
    for raw in _URL_PATTERN.findall(text):
        candidate = raw.rstrip(".,;:'\"")
        try:
            parsed = urlsplit(candidate)
            if parsed.hostname not in _ACADEMIC_HOSTS:
                continue
            path = parsed.path.rstrip("/") or "/"
            urls.add(urlunsplit(parsed._replace(path=path)))
        except ValueError:
            continue
    return urls


def _normalized_evidence_urls(calls: tuple[ToolCallEvidence, ...]) -> set[str]:
    return {
        normalized
        for call in calls
        for value in call.result_urls
        for normalized in _normalized_academic_urls(value)
    }


def _is_ordered(expected: list[str], actual: list[str]) -> bool:
    iterator = iter(actual)
    return all(any(value == wanted for value in iterator) for wanted in expected)


class ToolRoutingJudge:
    """Pure, deterministic judge over telemetry-derived tool calls."""

    def judge(self, case: EvaluationCase, evidence: TurnEvidence, *, final_text: str | None = None) -> CaseResult:
        expectation = case.expectation
        if evidence.trace_id is None or evidence.trace_status != "ok":
            return CaseResult(
                case_id=case.id, verdict="BLOCKED", score=0,
                hard_failures=("trace_missing_or_incomplete",), final_text=final_text,
            )

        calls = evidence.calls
        actual_order = [call.tool_name for call in calls]
        actual = set(actual_order)
        required = set(expectation.required_tools)
        forbidden = set(expectation.forbidden_tools)
        delegation_required = expectation.delegation_policy == "required"
        delegation_preference_met = (
            len(evidence.workers) >= expectation.min_workers
            and len(evidence.dispatches) >= expectation.min_dispatches
            and (
                not expectation.worker_required_tools
                or set(expectation.worker_required_tools)
                <= {tool for worker in evidence.workers for tool in worker.tool_names}
            )
        )
        failures: list[str] = []
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        prohibited = sorted(actual & forbidden)
        if missing:
            failures.append(f"missing_required:{','.join(missing)}")
        if prohibited:
            failures.append(f"forbidden_called:{','.join(prohibited)}")
        if expectation.expected_no_tool and actual:
            failures.append(f"tool_called_for_no_tool_case:{','.join(sorted(actual))}")
        elif not expectation.allow_extra_tools and unexpected:
            failures.append(f"unexpected_called:{','.join(unexpected)}")
        if expectation.ordered_tools and not _is_ordered(expectation.ordered_tools, actual_order):
            failures.append("ordered_tools_violation")
        bad_status = [call.tool_name for call in calls if call.status not in expectation.allowed_tool_statuses]
        if bad_status:
            failures.append(f"unexpected_status:{','.join(bad_status)}")
        if delegation_required and len(evidence.workers) < expectation.min_workers:
            failures.append(f"insufficient_workers:{len(evidence.workers)}/{expectation.min_workers}")
        if expectation.max_workers is not None and len(evidence.workers) > expectation.max_workers:
            failures.append(f"excessive_workers:{len(evidence.workers)}/{expectation.max_workers}")
        if delegation_required and expectation.worker_required_tools:
            worker_tools = {tool for worker in evidence.workers for tool in worker.tool_names}
            missing_worker_tools = sorted(set(expectation.worker_required_tools) - worker_tools)
            if missing_worker_tools:
                failures.append(f"worker_missing_tools:{','.join(missing_worker_tools)}")
        if delegation_required and len(evidence.dispatches) < expectation.min_dispatches:
            failures.append(f"insufficient_dispatches:{len(evidence.dispatches)}/{expectation.min_dispatches}")
        if expectation.max_dispatches is not None and len(evidence.dispatches) > expectation.max_dispatches:
            failures.append(f"excessive_dispatches:{len(evidence.dispatches)}/{expectation.max_dispatches}")
        if expectation.require_join and any(not item.join for item in evidence.dispatches):
            failures.append("worker_dispatch_not_joined")
        if expectation.required_wait_mode and any(item.wait_mode != expectation.required_wait_mode for item in evidence.dispatches):
            failures.append(f"unexpected_wait_mode:{expectation.required_wait_mode}")
        if any(item.directive_chars == 0 for item in evidence.dispatches):
            failures.append("empty_worker_directive")
        if expectation.orchestration_mode == "parallel" and len(evidence.workers) > 1:
            attempts = evidence.worker_attempts or evidence.workers
            intervals = [(item.started_at, item.completed_at) for item in attempts if item.started_at and item.completed_at]
            if len(intervals) > 1 and not any(a[0] < b[1] and b[0] < a[1] for index, a in enumerate(intervals) for b in intervals[index + 1:]):
                failures.append("parallel_overlap_missing")
        if expectation.orchestration_mode == "sequential" and len(evidence.workers) > 1:
            ordered = sorted(evidence.workers, key=lambda item: item.started_at or datetime.min.replace(tzinfo=timezone.utc))
            if any(left.completed_at and right.started_at and left.completed_at > right.started_at for left, right in zip(ordered, ordered[1:])):
                failures.append("sequential_dependency_violation")
        final_lower = (final_text or "").lower()
        missing_terms = [term for term in expectation.final_response_terms if term.lower() not in final_lower]
        if missing_terms:
            failures.append(f"final_response_missing_terms:{','.join(missing_terms)}")
        citation_integrity = 1.0
        if expectation.require_citation_integrity:
            final_urls = _normalized_academic_urls(final_text or "")
            evidence_urls = _normalized_evidence_urls(calls)
            unsupported_urls = sorted(final_urls - evidence_urls)
            if unsupported_urls:
                citation_integrity = 0.0
                failures.append("citation_not_in_tool_result")

        business_order = [tool for tool in actual_order if tool not in _ORCHESTRATION_TOOLS]
        business_actual = set(business_order)
        business_unexpected = business_actual - required
        matched = business_actual & required
        recall = len(matched) / len(required) if required else 1.0
        precision = len(matched) / len(business_actual) if business_actual else 1.0
        status_score = (
            sum(
                any(call.tool_name == tool and call.status in expectation.allowed_tool_statuses for call in calls)
                for tool in required
            ) / len(required)
            if required else 1.0
        )
        order_score = 1.0 if not expectation.ordered_tools or "ordered_tools_violation" not in failures else 0.0
        repeats = len(business_order) - len(business_actual)
        efficiency = max(0.0, 1.0 - (repeats + len(business_unexpected)) / max(1, len(business_order)))
        score = round(100 * (0.45 * recall + 0.25 * precision + 0.15 * status_score + 0.10 * order_score + 0.05 * efficiency), 2)
        critical = "critical" in case.tags
        verdict = "PASS" if not failures and score >= 90 else "WARN" if not failures and score >= 75 else "FAIL"
        if critical and failures:
            verdict = "CRITICAL_FAIL"
        return CaseResult(
            case_id=case.id, verdict=verdict, score=score, hard_failures=tuple(failures),
            metrics={"tool_precision": precision, "tool_recall": recall, "status_score": status_score, "order_score": order_score, "efficiency": efficiency, "worker_count": float(len(evidence.workers)), "worker_success_rate": (sum(worker.status == "ok" for worker in evidence.workers) / len(evidence.workers) if evidence.workers else (1.0 if expectation.min_workers == 0 else 0.0)), "delegation_preference_met": float(delegation_preference_met), "citation_integrity": citation_integrity},
            trace_id=evidence.trace_id, tool_calls=calls, final_text=final_text,
        )
