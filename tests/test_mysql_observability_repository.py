from datetime import datetime, timezone

from core.observability.mysql_repository import MySQLTelemetryRepository


def test_usage_aggregates_span_rows_for_monitor_contract(monkeypatch) -> None:
    repository = object.__new__(MySQLTelemetryRepository)
    day = datetime.now(timezone.utc).date().isoformat()
    rows = [
        {
            "completed_at": f"{day}T10:00:00+00:00",
            "kind": "model",
            "name": "coordinator.model",
            "attributes": {"model": "test-model"},
            "status": "ok",
            "duration_ms": 125,
            "input_tokens": 12,
            "output_tokens": 3,
            "cached_tokens": 2,
            "cache_miss_tokens": 10,
            "reasoning_tokens": 1,
            "total_tokens": 15,
        },
        {
            "completed_at": f"{day}T11:00:00+00:00",
            "kind": "model",
            "name": "coordinator.model",
            "attributes": {"model": "test-model"},
            "status": "error",
            "duration_ms": 75,
            "input_tokens": 8,
            "output_tokens": 2,
            "cached_tokens": 0,
            "cache_miss_tokens": 8,
            "reasoning_tokens": 0,
            "total_tokens": 10,
        },
    ]

    monkeypatch.setattr(repository, "_rows", lambda kind: rows if kind == "span" else [])

    assert repository.usage() == [
        {
            "day": day,
            "component": "model",
            "name": "coordinator.model:test-model",
            "requests": 2,
            "successes": 1,
            "errors": 1,
            "duration_sum_ms": 200,
            "input_tokens": 20,
            "output_tokens": 5,
            "cached_tokens": 2,
            "cache_miss_tokens": 18,
            "reasoning_tokens": 1,
            "total_tokens": 25,
        }
    ]