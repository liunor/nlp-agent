from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from evaluation.core.catalog import discover_suites, resolve_suite_reference
from evaluation.core.dataset import dataset_summary, load_dataset
from evaluation.core.reporting import load_report, load_trace_metrics, render_report
from evaluation.core.runner import EvaluationRunner, MonitorHttpEvidenceReader, RemoteApiExecutor


def _evaluation_credentials() -> tuple[str, str]:
    dotenv = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    username = (
        os.environ.get("NLP_AGENT_EVALUATION_USERNAME")
        or str(dotenv.get("NLP_AGENT_EVALUATION_USERNAME") or "")
    ).strip()
    password = os.environ.get("NLP_AGENT_EVALUATION_PASSWORD") or str(
        dotenv.get("NLP_AGENT_EVALUATION_PASSWORD") or ""
    )
    return username, password


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python -m evaluation")
    commands = value.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("dataset", type=Path)
    run = commands.add_parser("run")
    run.add_argument("suite", help="suite id (recommended) or a legacy dataset YAML path")
    run.add_argument("--live", action="store_true", help="allow real model API calls and associated cost")
    run.add_argument("--case", action="append", default=[])
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--workspace", default="default")
    run.add_argument("--timeout", type=float, default=90)
    run.add_argument("--web-url", default="http://127.0.0.1:8765")
    run.add_argument("--monitor-url", default="http://127.0.0.1:8766")
    run.add_argument("--output", type=Path)
    report = commands.add_parser("report", help="render a terminal summary from a saved live evaluation report")
    report.add_argument("report", type=Path)
    report.add_argument("--telemetry-db", type=Path, default=Path(".data/observability/telemetry.sqlite3"))
    report.add_argument("--case", action="append", default=[])
    report.add_argument("--failed-only", action="store_true")
    report.add_argument("--details", action="store_true", help="include shortened final responses")
    return value


async def _run(args: argparse.Namespace) -> int:
    if not args.live:
        raise SystemExit("Real model execution is disabled. Re-run with --live after confirming API cost.")
    suite = resolve_suite_reference(args.suite, suites=discover_suites())
    dataset, digest = load_dataset(suite.dataset_path)
    cases = [case for case in dataset.cases if not args.case or case.id in args.case]
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        raise SystemExit("No cases selected")
    username, password = _evaluation_credentials()
    if bool(username) != bool(password):
        raise SystemExit(
            "Set both NLP_AGENT_EVALUATION_USERNAME and "
            "NLP_AGENT_EVALUATION_PASSWORD, or leave both unset for an "
            "explicitly enabled guest/test adapter."
        )
    executor = RemoteApiExecutor(
        args.web_url,
        username=username or None,
        password=password or None,
    )
    evidence = MonitorHttpEvidenceReader(
        args.monitor_url,
        timeout_s=args.timeout,
        session_cookie_provider=executor.session_cookies,
    )
    runner = EvaluationRunner(executor, evidence)
    report = await runner.run(suite_id=dataset.suite["id"], dataset_sha256=digest, cases=cases, workspace_id=args.workspace, timeout_s=args.timeout)
    rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    print(rendered)
    target = args.output or suite.runs_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{report.run_id[:8]}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered + "\n", encoding="utf-8")
    print(f"Saved evaluation report: {target}")
    return 0 if report.verdict == "PASS" else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args()
    if args.command == "validate":
        dataset, digest = load_dataset(args.dataset)
        print(json.dumps({**dataset_summary(dataset), "sha256": digest}, ensure_ascii=False, indent=2))
        return
    if args.command == "report":
        report = load_report(args.report)
        trace_ids = {item.trace_id for item in report.results if item.trace_id}
        telemetry = load_trace_metrics(args.telemetry_db, trace_ids)
        print(render_report(report, telemetry, case_ids=set(args.case) or None, failed_only=args.failed_only, details=args.details))
        return
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
