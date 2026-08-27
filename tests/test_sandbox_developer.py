from __future__ import annotations


def test_runtime_state_summary_has_dashboard_states() -> None:
    from server.sandbox.developer import summarize_runtime_states

    assert summarize_runtime_states([("ready_unbound", 2), ("failed", 1)]) == {
        "creating": 0, "ready_unbound": 2, "claiming": 0, "assigned": 0, "draining": 0, "failed": 1,
    }


def test_developer_catalog_exposes_sandbox_route() -> None:
    from server.rbac.catalog import MENU_CATALOG

    assert any(item[2] == "/developer/sandbox" for item in MENU_CATALOG)


def test_capacity_snapshot_reports_pool_deficit() -> None:
    from server.sandbox.developer import capacity_snapshot

    assert capacity_snapshot({"ready_unbound": 1, "creating": 2}, target=5) == {
        "ready": 1, "creating": 2, "target": 5, "deficit": 2,
    }


def test_execution_latency_has_dashboard_percentiles() -> None:
    from server.sandbox.developer import summarize_execution_latency

    assert summarize_execution_latency([10, 20, 30, 40, 50]) == {
        "sample_count": 5, "p50_ms": 30, "p95_ms": 40, "p99_ms": 40,
    }


def test_developer_router_exposes_runtime_inventory_and_drain() -> None:
    from server.sandbox.developer_controller import router

    paths = {route.path for route in router.routes}
    assert "/api/v1/developer/sandbox/runtimes" in paths
    assert "/api/v1/developer/sandbox/runtimes/{runtime_id}/drain" in paths
    assert "/api/v1/developer/sandbox/runtimes/{runtime_id}" in paths
    assert "/api/v1/developer/sandbox/executions" in paths
    assert "/api/v1/developer/sandbox/executions/{execution_id}/events" in paths
    assert "/api/v1/developer/sandbox/preload-compatibility" in paths
    assert "/api/v1/developer/sandbox/capacity/prewarm" in paths


def test_sandbox_router_exposes_server_issued_confirmation_endpoint() -> None:
    from server.sandbox.controller import router

    paths = {route.path for route in router.routes}
    assert "/api/v1/sandbox/confirmations" in paths
