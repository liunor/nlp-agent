from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from evaluation.core.models import EvaluationReport


@dataclass(frozen=True)
class TraceMetrics:
    status: str
    duration_ms: int | None
    ttft_ms: int | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_tokens: int
    model_spans: int
    tool_spans: int


def load_report(path: Path) -> EvaluationReport:
    return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_trace_metrics(path: Path, trace_ids: set[str]) -> dict[str, TraceMetrics]:
    """Read telemetry in SQLite read-only mode so reporting cannot mutate live data."""
    if not path.exists():
        return {}
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in trace_ids)
        if not placeholders:
            return {}
        trace_rows = connection.execute(
            f"SELECT trace_id, status, duration_ms, ttft_ms, input_tokens, output_tokens, "
            f"cached_tokens, total_tokens FROM traces WHERE trace_id IN ({placeholders})",
            tuple(trace_ids),
        ).fetchall()
        span_rows = connection.execute(
            f"SELECT trace_id, kind, COUNT(*) FROM spans WHERE trace_id IN ({placeholders}) "
            "GROUP BY trace_id, kind",
            tuple(trace_ids),
        ).fetchall()
    finally:
        connection.close()
    span_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for trace_id, kind, count in span_rows:
        span_counts[trace_id][kind] = count
    return {
        trace_id: TraceMetrics(
            status=status,
            duration_ms=duration_ms,
            ttft_ms=ttft_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            model_spans=span_counts[trace_id]["model"],
            tool_spans=span_counts[trace_id]["tool"],
        )
        for trace_id, status, duration_ms, ttft_ms, input_tokens, output_tokens, cached_tokens, total_tokens in trace_rows
    }


def render_report(report: EvaluationReport, telemetry: dict[str, TraceMetrics], *, case_ids: set[str] | None = None, failed_only: bool = False, details: bool = False) -> str:
    results = [item for item in report.results if (case_ids is None or item.case_id in case_ids)]
    if failed_only:
        results = [item for item in results if item.verdict != "PASS"]
    verdicts = Counter(item.verdict for item in results)
    trace_rows = [telemetry[item.trace_id] for item in results if item.trace_id in telemetry]
    lines = [
        "NLP Agent Evaluation Report",
        "=" * 78,
        f"run: {report.run_id}  suite: {report.suite_id}  verdict: {report.verdict}",
        f"period: {report.started_at.isoformat()} -> {report.completed_at.isoformat()}",
        f"shown cases: {len(results)}  PASS: {verdicts['PASS']}  WARN: {verdicts['WARN']}  FAIL: {verdicts['FAIL']}  CRITICAL_FAIL: {verdicts['CRITICAL_FAIL']}  BLOCKED: {verdicts['BLOCKED']}",
        f"worker orchestration: avg workers/case={mean(item.metrics.get('worker_count', 0) for item in results):.2f}  worker success={_pct(mean(item.metrics.get('worker_success_rate', 1) for item in results)) if results else '-'}",
        "",
        "Routing quality",
        _table(
            ["case pass", "critical pass", "trace capture", "macro tool F1"],
            [[_pct(report.metrics.get("case_pass_rate")), _pct(report.metrics.get("critical_pass_rate")), _pct(report.metrics.get("trace_capture_rate")), _pct(report.metrics.get("macro_tool_f1"))]],
        ),
    ]
    if trace_rows:
        lines.extend([
            "",
            "Observed trace / model metrics (from Monitor telemetry)",
            _table(
                ["traces", "trace ok", "avg latency", "p95 latency", "avg TTFT", "total tokens", "avg tokens", "model spans", "tool spans"],
                [[
                    str(len(trace_rows)),
                    _pct(sum(item.status == "ok" for item in trace_rows) / len(trace_rows)),
                    _ms(mean_known(item.duration_ms for item in trace_rows)),
                    _ms(_percentile([item.duration_ms for item in trace_rows if item.duration_ms is not None], .95)),
                    _ms(mean_known(item.ttft_ms for item in trace_rows)),
                    f"{sum(item.total_tokens for item in trace_rows):,}",
                    f"{mean(item.total_tokens for item in trace_rows):,.0f}",
                    str(sum(item.model_spans for item in trace_rows)),
                    str(sum(item.tool_spans for item in trace_rows)),
                ]],
            ),
        ])
    else:
        lines.extend(["", "Observed trace / model metrics: unavailable (no matching records in telemetry database)."])

    if "academic_search_recall" in report.metrics:
        lines.extend(
            [
                "",
                "Academic search quality",
                _table(
                    ["search recall", "false positive rate", "citation integrity"],
                    [[
                        _pct(report.metrics.get("academic_search_recall")),
                        _pct(report.metrics.get("academic_false_positive_rate")),
                        _pct(report.metrics.get("citation_integrity_rate")),
                    ]],
                ),
            ]
        )

    tools: dict[str, list[tuple[Any, int]]] = defaultdict(list)
    for item in results:
        for call in item.tool_calls:
            tools[call.tool_name].append((item, call.duration_ms))
    lines.extend(["", "Tool-level averages"])
    lines.append(_table(
        ["tool", "calls", "cases", "ok", "avg tool ms", "p95 tool ms", "avg case score", "failed cases"],
        [[
            tool,
            str(len(calls)),
            str(len({item.case_id for item, _ in calls})),
            _pct(sum(call.status == "ok" for item in results for call in item.tool_calls if call.tool_name == tool) / len(calls)),
            _ms(mean(duration for _, duration in calls)),
            _ms(_percentile([duration for _, duration in calls], .95)),
            f"{mean(item.score for item, _ in calls):.2f}",
            str(len({item.case_id for item, _ in calls if item.verdict != "PASS"})),
        ] for tool, calls in sorted(tools.items())],
    ))

    lines.extend(["", "Case details"])
    detail_rows = []
    for item in results:
        trace = telemetry.get(item.trace_id or "")
        detail_rows.append([
            item.case_id,
            item.verdict,
            f"{item.score:.2f}",
            ", ".join(call.tool_name for call in item.tool_calls) or "-",
            _worker_allocation(item.tool_calls),
            _ms(trace.duration_ms) if trace else "-",
            f"{trace.total_tokens:,}" if trace else "-",
            "; ".join(item.hard_failures) or "-",
        ])
    lines.append(_table(["case", "verdict", "score", "called tools", "worker allocation", "latency", "tokens", "routing issue"], detail_rows))
    if details:
        lines.extend(["", "Response excerpts"])
        for item in results:
            excerpt = " ".join((item.final_text or "").split())
            lines.append(f"- {item.case_id}: {excerpt[:300]}{'…' if len(excerpt) > 300 else ''}")
    return "\n".join(lines)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    separator = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    rendered = [separator, "|" + "|".join(f" {value:<{widths[index]}} " for index, value in enumerate(headers)) + "|", separator]
    rendered.extend("|" + "|".join(f" {value:<{widths[index]}} " for index, value in enumerate(row)) + "|" for row in rows)
    rendered.append(separator)
    return "\n".join(rendered)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def _ms(value: float | int | None) -> str:
    return "-" if value is None else f"{value:,.0f} ms"


def mean_known(values: Any) -> float | None:
    known = [value for value in values if value is not None]
    return mean(known) if known else None


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percentile)]


def _worker_allocation(calls: tuple[Any, ...]) -> str:
    allocated: dict[str, list[str]] = defaultdict(list)
    for call in calls:
        if call.worker_id:
            allocated[call.worker_id].append(call.tool_name)
    return "; ".join(f"{worker}:{','.join(tools)}" for worker, tools in sorted(allocated.items())) or "coordinator-only"
