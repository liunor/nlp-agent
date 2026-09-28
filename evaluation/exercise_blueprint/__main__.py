from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from evaluation.exercise_blueprint.dataset import load_exercise_dataset
from evaluation.exercise_blueprint.http_executor import HttpExerciseGatewayExecutor
from evaluation.exercise_blueprint.runner import ExerciseEvaluationRunner
from evaluation.exercise_blueprint.student_simulator import FlashExerciseStudentSimulator
from server.quota.bootstrap import configure_usage_reporter, shutdown_usage_reporter


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python -m evaluation.exercise_blueprint")
    value.add_argument("suite", type=Path)
    value.add_argument("--live", action="store_true", help="allow Flash student-model API calls")
    value.add_argument("--workspace", default="evaluation-exercise")
    value.add_argument("--web-url", default="http://127.0.0.1:8765")
    value.add_argument("--timeout", type=float, default=120)
    value.add_argument("--case", action="append", default=[])
    value.add_argument("--provision-fixture", action="store_true")
    value.add_argument("--output", type=Path)
    return value


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise SystemExit("Real evaluation is disabled. Re-run with --live after confirming API cost.")
    usage_reporter = configure_usage_reporter(required=True)
    try:
        dataset, digest = load_exercise_dataset(args.suite)
        cases = [case for case in dataset.cases if not args.case or case.id in args.case]
        if not cases:
            raise SystemExit("No exercise cases selected")
        executor = HttpExerciseGatewayExecutor(args.web_url, workspace_id=args.workspace, suite_id=dataset.suite.id, timeout_s=args.timeout)
        try:
            if args.provision_fixture:
                await executor.provision_fixture(dataset.blueprint)
            runner = ExerciseEvaluationRunner(executor, FlashExerciseStudentSimulator())
            outcomes = []
            for case in cases:
                snapshot, architecture = await runner.run_case(case=case, blueprint=dataset.blueprint)
                outcomes.append({"case_id": case.id, "snapshot": snapshot.model_dump(mode="json"), "architecture": architecture.model_dump(mode="json")})
        finally:
            await executor.close()
        payload = {"run_id": executor.run_id, "suite_id": dataset.suite.id, "dataset_sha256": digest, "created_at": datetime.now().astimezone().isoformat(), "outcomes": outcomes, "verdict": "PASS" if all(item["architecture"]["verdict"] == "PASS" for item in outcomes) else "FAIL"}
        target = args.output or args.suite.parent.parent.parent / "runs" / dataset.suite.id / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{executor.run_id[:8]}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"Saved exercise blueprint evaluation report: {target}")
        return 0 if payload["verdict"] == "PASS" else 1
    finally:
        shutdown_usage_reporter(usage_reporter)


def main() -> None:
    raise SystemExit(asyncio.run(run(parser().parse_args())))


if __name__ == "__main__":
    main()
