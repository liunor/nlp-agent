"""Ensure test-only timings preserve execution and never capture payloads."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.load.submission_diagnostics import SubmissionTimings, install


def test_real_fixture_models_and_gateway_instrumentation_import_without_database_io():
    result = subprocess.run([sys.executable, "-c", "from tests.load.model_configuration import configure_models; "
                             "configure_models(); from tests.load.submission_diagnostics import install; "
                             "install(); print('ISOLATED_OBSERVER_INSTALLED')"],
                            cwd=Path(__file__).parents[1],
                            env={**os.environ,
                                 "NLP_AGENT_DEPLOYMENT_ENV": "test", "T07_LOAD_RUN_KIND": "regression",
                                 "NLP_AGENT_DATABASE_URL": "mysql+aiomysql://fixture:fixture@127.0.0.1:1/nlp_agent_api_http_only",
                                 "T07_FAKE_PROVIDER_CONTROL_URL": "http://127.0.0.1:18767"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ISOLATED_OBSERVER_INSTALLED" in result.stdout


def test_observer_refuses_live_or_baseline_database(monkeypatch):
    monkeypatch.setenv("NLP_AGENT_DEPLOYMENT_ENV", "test")
    monkeypatch.setenv("T07_LOAD_RUN_KIND", "regression")
    monkeypatch.setenv("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://secret@127.0.0.1/business")
    with pytest.raises(ValueError, match="DISPOSABLE"):
        install()


def test_context_crosses_threads_but_does_not_record_unrelated_calls(capsys):
    timings = SubmissionTimings()
    worker = timings.wrap(lambda value: value + 1, "repository_create_turn")
    assert worker(1) == 2
    assert timings.snapshot() == {}

    async def submit(value):
        return await asyncio.to_thread(worker, value)

    observed = timings.wrap(submit, "gateway_submit_total", root=True)
    assert asyncio.run(observed(9)) == 10
    assert not timings.active.get()
    snapshot = timings.snapshot()
    assert snapshot["repository_create_turn"]["count"] == snapshot["gateway_submit_total"]["count"] == 1
    assert snapshot["gateway_submit_total"]["failures"] == 0
    assert "T07_SUBMISSION_DIAGNOSTICS" in capsys.readouterr().out


def test_exception_and_cancellation_preserved_and_no_payload_logged(capsys):
    timings = SubmissionTimings()

    async def failed(secret):
        raise asyncio.CancelledError(secret)

    observed = timings.wrap(failed, "gateway_submit_total", root=True)
    with pytest.raises(asyncio.CancelledError, match="private-payload"):
        asyncio.run(observed("private-payload"))
    assert timings.snapshot()["gateway_submit_total"]["failures"] == 1
    assert "private-payload" not in capsys.readouterr().out
    assert not timings.active.get()


def test_timing_storage_is_bounded_and_json_contains_only_fixed_stage_numbers():
    timings = SubmissionTimings()
    for _ in range(5000):
        timings.record("sql_other", 0)
    snapshot = timings.snapshot()
    assert snapshot["sql_other"]["count"] == 5000
    assert snapshot["sql_other"]["sampled"] == 4096
    assert json.loads(json.dumps(snapshot)) == snapshot
