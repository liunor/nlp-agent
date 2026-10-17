from __future__ import annotations

from pathlib import Path

import pytest

from tests.load.t07_classroom_load import LoadTestError, validate_base_url, validate_output_path


def test_load_runner_accepts_only_absolute_http_targets() -> None:
    assert validate_base_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    with pytest.raises(LoadTestError, match="absolute http"):
        validate_base_url("file:///tmp/production")


@pytest.mark.parametrize(
    "url",
    [
        "https://api-production.example.test",
        "https://live.example.test",
        "http://user:password@127.0.0.1:8765",
    ],
)
def test_load_runner_rejects_production_hosts_and_embedded_credentials(url: str) -> None:
    with pytest.raises(LoadTestError):
        validate_base_url(url)


def test_load_runner_refuses_report_overwrite_and_paths_outside_t07_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.load import t07_classroom_load

    monkeypatch.setattr(t07_classroom_load, "ROOT", tmp_path)
    allowed = Path("artifacts/load-tests/t07/baseline.json")
    assert validate_output_path(str(allowed)).name == "baseline.json"

    outside = tmp_path / "baseline.json"
    with pytest.raises(LoadTestError, match="inside artifacts"):
        validate_output_path(str(outside))

    existing = tmp_path / "artifacts" / "load-tests" / "t07" / "baseline.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(LoadTestError, match="overwrite"):
        validate_output_path(str(existing))


def test_baseline_json_requires_a_new_summary_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.load import t07_classroom_load

    monkeypatch.setattr(t07_classroom_load, "ROOT", tmp_path)
    report = tmp_path / "artifacts" / "load-tests" / "t07" / "baseline.json"
    report.parent.mkdir(parents=True)
    report.with_name("baseline-summary.md").write_text("keep", encoding="utf-8")

    with pytest.raises(LoadTestError, match="baseline-summary"):
        validate_output_path(str(report))


def test_current_regression_cannot_be_named_pre_fix_baseline():
    from argparse import Namespace
    from tests.load.t07_classroom_load import validate_run_kind

    with pytest.raises(LoadTestError, match="isolated old version"):
        validate_run_kind(Namespace(run_kind="regression"), Path("baseline.json"))


def test_baseline_requires_frozen_ref_and_unchanged_product_code(monkeypatch):
    from argparse import Namespace
    from subprocess import CompletedProcess
    from unittest.mock import Mock
    from tests.load import t07_classroom_load as load

    reference = load.BASELINE_PRODUCT_COMMIT
    mocked = Mock(side_effect=[
        CompletedProcess([], 0, reference + "\n"),
        CompletedProcess([], 0, reference + "\n"),
        CompletedProcess([], 0, " M gateway/core.py\n"),
    ])
    monkeypatch.setattr(load.subprocess, "run", mocked)
    with pytest.raises(LoadTestError, match="unchanged product code"):
        load.validate_run_kind(Namespace(run_kind="baseline", baseline_ref=reference), Path("baseline.json"))


@pytest.mark.asyncio
async def test_baseline_rejects_regression_target_even_from_frozen_checkout(tmp_path, monkeypatch):
    import json
    from argparse import Namespace
    from unittest.mock import AsyncMock
    import httpx
    from tests.load import t07_classroom_load as load

    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"version": 1, "scenarios": [], "user_count": 50, "turn_count": 30}), encoding="utf-8")
    monkeypatch.setattr(load, "ROOT", tmp_path)
    monkeypatch.setattr(load, "validate_run_kind", lambda *_args: None)  # frozen local product already validated
    monkeypatch.setattr(load, "_profile_users", lambda _profile: [{} for _ in range(50)])
    monkeypatch.setattr(load, "_fake_provider_control", AsyncMock(side_effect=AssertionError("must reject the wrong target before touching Provider control")))
    client = httpx.AsyncClient(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ready": True, "deployment_environment": "test"})))
    monkeypatch.setattr(load.httpx, "AsyncClient", lambda **_kwargs: client)
    args = Namespace(base_url="http://127.0.0.1:8765", output="artifacts/load-tests/t07/baseline.json", profile=str(profile), run_kind="baseline")
    with pytest.raises(LoadTestError, match="target.*commit"):
        await load.run(args)
