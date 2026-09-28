import subprocess
import sys
from pathlib import Path

from datetime import datetime, timezone

from scripts.seed_monitor_demo import (
    MARKER,
    _build_observability,
    _build_sandbox_demo,
    is_local_endpoint,
)


def test_demo_seed_builds_dense_multi_user_observability_data():
    envelopes, usage_rows = _build_observability(count=96)

    traces = [envelope for envelope in envelopes if envelope.kind == "trace"]
    spans = [envelope for envelope in envelopes if envelope.kind == "span"]
    events = [envelope for envelope in envelopes if envelope.kind == "event"]

    assert len(traces) == 96
    assert len(spans) == 96 * 3
    assert len(events) >= 96
    assert any(event.payload.name == "request.slow" for event in events)
    assert any(event.payload.name == "telemetry.backpressure" and event.payload.trace_id is None for event in events)
    assert len(usage_rows) == 96
    assert {row["user_id"] for row in usage_rows} == {
        "demo-user-alice",
        "demo-user-bob",
        "demo-user-charlie",
        "demo-user-diana",
    }
    assert len({row["provider_model"] for row in usage_rows}) == 3
    assert {row["raw_usage_json"]["demo_seed_id"] for row in usage_rows} == {MARKER}
    assert {row["outcome_status"] for row in usage_rows} >= {"completed", "error", "timeout"}


def test_demo_seed_script_supports_direct_project_root_invocation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/seed_monitor_demo.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "Seed synthetic monitoring data" in result.stdout


def test_demo_seed_builds_bounded_sandbox_monitor_data_without_user_code():
    users, workspace, environments, runtimes, executions, samples = _build_sandbox_demo(
        count=24,
        now=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert len(users) == 4
    assert workspace["slug"] == f"{MARKER}-workspace"
    assert len(environments) == 4
    assert all("active_runtime_id" in env for env in environments)
    assert sum(env["active_runtime_id"] is not None for env in environments) >= 1
    assert len(runtimes) == 12
    assert len(executions) == 24
    assert len(samples) == 60
    assert {row["state"] for row in runtimes} >= {
        "ready_unbound",
        "assigned",
        "creating",
        "claiming",
        "draining",
        "failed",
    }
    assert {row["status"] for row in executions} >= {"running", "completed", "failed", "timeout"}
    assert all(row["external_runtime_id"].startswith(f"{MARKER}-runtime-") for row in runtimes)
    assert all(row["request_id"].startswith(f"{MARKER}-sandbox-request-") for row in executions)
    assert all(row["resource_summary_json"]["demo_seed_id"] == MARKER for row in executions)
    assert all("code" not in row["resource_summary_json"] for row in executions)
    assert {sample["demo_seed_id"] for sample in samples} == {MARKER}


def test_demo_seed_accepts_only_exact_loopback_endpoints():
    assert is_local_endpoint("mysql+aiomysql://user:secret@localhost:3306/nlp")
    assert is_local_endpoint("redis://127.0.0.1:6379/0")
    assert is_local_endpoint("redis://[::1]:6379/0")
    assert not is_local_endpoint("mysql+aiomysql://user:localhost@db.internal/nlp")
    assert not is_local_endpoint("redis://127.0.0.1.evil.example/0")
