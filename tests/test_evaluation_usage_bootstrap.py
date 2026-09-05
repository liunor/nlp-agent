from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeSnapshot:
    def model_dump(self, *, mode: str):
        return {"mode": mode}


class _FakeArchitecture:
    def model_dump(self, *, mode: str):
        return {"mode": mode, "verdict": "PASS"}


class _FakeExecutor:
    def __init__(self, *args, **kwargs):
        self.run_id = "run-1"

    async def close(self):
        return None


class _FakeRunner:
    def __init__(self, *args, **kwargs):
        return None

    async def run_case(self, *, case, blueprint):
        return _FakeSnapshot(), _FakeArchitecture()


class _FakeStudentSimulator:
    def __init__(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "loader_name", "executor_name", "runner_name", "simulator_name"),
    [
        (
            "evaluation.exercise_blueprint.__main__",
            "load_exercise_dataset",
            "HttpExerciseGatewayExecutor",
            "ExerciseEvaluationRunner",
            "FlashExerciseStudentSimulator",
        ),
        (
            "evaluation.guided.__main__",
            "load_guided_dataset",
            "HttpGuidedGatewayExecutor",
            "GuidedEvaluationRunner",
            "FlashStudentSimulator",
        ),
        (
            "evaluation.review_blueprint.__main__",
            "load_review_dataset",
            "HttpReviewGatewayExecutor",
            "ReviewEvaluationRunner",
            "FlashReviewStudentSimulator",
        ),
    ],
)
async def test_live_evaluation_bootstraps_usage_reporter(
    monkeypatch,
    module_name: str,
    loader_name: str,
    executor_name: str,
    runner_name: str,
    simulator_name: str,
):
    module = importlib.import_module(module_name)
    suite = SimpleNamespace(id="suite-1")
    dataset = SimpleNamespace(
        suite=suite,
        cases=[SimpleNamespace(id="case-1")],
        blueprint={},
    )
    monkeypatch.setattr(module, loader_name, lambda _path: (dataset, "digest"))
    monkeypatch.setattr(module, executor_name, _FakeExecutor)
    monkeypatch.setattr(module, runner_name, _FakeRunner)
    monkeypatch.setattr(module, simulator_name, _FakeStudentSimulator)
    monkeypatch.setattr(Path, "write_text", lambda *args, **kwargs: 0)

    configured = []
    shutdown = []
    reporter = object()

    monkeypatch.setattr(
        module,
        "configure_usage_reporter",
        lambda **kwargs: configured.append(kwargs) or reporter,
    )
    monkeypatch.setattr(
        module,
        "shutdown_usage_reporter",
        lambda value: shutdown.append(value),
    )

    args = SimpleNamespace(
        live=True,
        suite=Path("suite.yaml"),
        case=[],
        workspace="evaluation-test",
        web_url="http://testserver",
        timeout=1.0,
        provision_fixture=False,
        output=Path("result.json"),
    )

    assert await module.run(args) == 0
    assert configured == [{"required": True}]
    assert shutdown == [reporter]
