"""PyCharm-friendly interactive generator for evaluation result Markdown files."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.core.reporting import load_report, load_trace_metrics
from evaluation.core.catalog import discover_suites, latest_run_path, resolve_suite

def render_markdown(report_path: Path, *, suite_name: str, telemetry_db: Path) -> str:
    report = load_report(report_path)
    telemetry = load_trace_metrics(telemetry_db, {item.trace_id for item in report.results if item.trace_id})
    results = list(report.results)
    verdicts = Counter(item.verdict for item in results)
    traces = [telemetry[item.trace_id] for item in results if item.trace_id in telemetry]
    failed = [item for item in results if item.verdict != "PASS"]
    lines = [
        f"# {suite_name}结果报告",
        "",
        f"- 报告文件：`{report_path.name}`",
        f"- 运行 ID：`{report.run_id}`",
        f"- 时间：{report.started_at.isoformat()} → {report.completed_at.isoformat()}",
        f"- 总结结论：**{report.verdict}**",
        "",
        "## 总结",
        "",
        f"- 用例：{len(results)}；通过：{verdicts['PASS']}；失败：{verdicts['FAIL']}；关键失败：{verdicts['CRITICAL_FAIL']}；阻塞：{verdicts['BLOCKED']}。",
        f"- 用例通过率：{_pct(report.metrics.get('case_pass_rate'))}；关键通过率：{_pct(report.metrics.get('critical_pass_rate'))}。",
        f"- 工具路由 Macro F1：{_pct(report.metrics.get('macro_tool_f1'))}；Trace 捕获率：{_pct(report.metrics.get('trace_capture_rate'))}。",
    ]
    if traces:
        lines.extend([
            f"- Monitor：{len(traces)}/{len(results)} 条 trace 已关联，trace 成功率 {_pct(sum(trace.status == 'ok' for trace in traces) / len(traces))}。",
            f"- 平均响应 {_ms(_mean([trace.duration_ms for trace in traces]))}；平均 TTFT {_ms(_mean([trace.ttft_ms for trace in traces]))}；总 Token {sum(trace.total_tokens for trace in traces):,}；平均 Token {_mean([trace.total_tokens for trace in traces]):,.0f}。",
        ])
    if any("worker_count" in item.metrics for item in results):
        lines.append(f"- Worker：平均每用例 {_mean([item.metrics.get('worker_count', 0) for item in results]):.2f} 个；平均成功率 {_pct(_mean([item.metrics.get('worker_success_rate', 1) for item in results]))}。")
    if "academic_search_recall" in report.metrics:
        lines.append(
            "- 学术检索：应检索召回率 "
            f"{_pct(report.metrics.get('academic_search_recall'))}；误触发率 "
            f"{_pct(report.metrics.get('academic_false_positive_rate'))}；引用完整率 "
            f"{_pct(report.metrics.get('citation_integrity_rate'))}。"
        )
    lines.extend(["", "## 工具维度", "", "| 工具 | 调用次数 | 覆盖用例 | 成功率 | 平均工具耗时 | 失败用例 |", "|---|---:|---:|---:|---:|---:|"])
    grouped: dict[str, list[tuple[object, int, str]]] = defaultdict(list)
    for result in results:
        for call in result.tool_calls:
            grouped[call.tool_name].append((result, call.duration_ms, call.status))
    for tool, calls in sorted(grouped.items()):
        lines.append(f"| `{tool}` | {len(calls)} | {len({item.case_id for item, _, _ in calls})} | {_pct(sum(status == 'ok' for _, _, status in calls) / len(calls))} | {_ms(_mean([duration for _, duration, _ in calls]))} | {len({item.case_id for item, _, _ in calls if item.verdict != 'PASS'})} |")
    lines.extend(["", "## 逐案例明细", "", "| 用例 | 结果 | 分数 | 工具调用 | Worker 分配 | 响应 | Token | 失败原因 |", "|---|---|---:|---|---|---:|---:|---|"])
    for result in results:
        trace = telemetry.get(result.trace_id or "")
        calls = ", ".join(call.tool_name for call in result.tool_calls) or "-"
        allocation = _worker_allocation(result.tool_calls)
        reason = "; ".join(result.hard_failures) or "-"
        lines.append(f"| `{result.case_id}` | {result.verdict} | {result.score:.2f} | {calls} | {allocation} | {_ms(trace.duration_ms) if trace else '-'} | {f'{trace.total_tokens:,}' if trace else '-'} | {reason} |")
    lines.extend(["", "## 客观问题归纳", ""])
    if not failed:
        lines.append("本次所有用例通过；仍建议结合逐案例明细观察 Token、响应时延与重复调用。")
    else:
        reasons = Counter(reason for result in failed for reason in result.hard_failures)
        for reason, count in reasons.most_common():
            lines.append(f"- `{reason}`：{count} 条用例。")
        lines.append("")
        lines.append("以上为基于评测断言与 Monitor 证据的事实归纳；工具路由通过不代表最终答案的数学正确性或教学质量已被验证。")
    lines.extend(["", "## 响应摘录", ""])
    for result in results:
        excerpt = " ".join((result.final_text or "").split())
        lines.append(f"### {result.case_id}\n\n{excerpt[:500]}{'…' if len(excerpt) > 500 else ''}\n")
    return "\n".join(lines)


def choose_suite() -> tuple[str, Path, Path]:
    suites = discover_suites()
    if not suites:
        raise FileNotFoundError("未发现评测套件；请在 .jbeval/suites/<suite-id>/ 放入 dataset.yaml")
    print("请选择要生成报告的评测项目：")
    ordered = list(suites.values())
    for index, suite in enumerate(ordered, start=1):
        latest = latest_run_path(suite.runs_dir)
        suffix = latest.name if latest else "暂无运行结果"
        print(f"  {index}. {suite.name} ({suite.id}) — {suffix}")
    choice = input("请输入序号或 suite id：").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(ordered):
        suite = ordered[int(choice) - 1]
    else:
        suite = resolve_suite(choice, suites=suites)
    latest = latest_run_path(suite.runs_dir)
    manual = input("直接回车使用最新报告；或输入任意 JSON 报告路径：").strip()
    path = Path(manual) if manual else latest
    if path is None or not path.is_file():
        raise FileNotFoundError(f"未找到运行结果：{path or suite.runs_dir}")
    return suite.name, path, suite.results_dir


def main() -> None:
    try:
        suite_name, report_path, output_dir = choose_suite()
        markdown = render_markdown(report_path, suite_name=suite_name, telemetry_db=ROOT / ".data" / "observability" / "telemetry.sqlite3")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{report_path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        target.write_text(markdown, encoding="utf-8")
        print(f"\n已生成：{target}")
    except (ValueError, FileNotFoundError) as error:
        print(f"\n未生成报告：{error}")


def _worker_allocation(calls: tuple[object, ...]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for call in calls:
        worker_id = getattr(call, "worker_id", None)
        if worker_id:
            grouped[worker_id].append(getattr(call, "tool_name", ""))
    return "; ".join(f"{worker}:{','.join(tools)}" for worker, tools in sorted(grouped.items())) or "Coordinator"


def _mean(values: list[int | float | None]) -> float:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else 0.0


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def _ms(value: float | int | None) -> str:
    return "-" if value is None else f"{value:,.0f} ms"


if __name__ == "__main__":
    main()
