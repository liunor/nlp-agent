from datetime import datetime, timezone

from sqlalchemy.dialects import mysql

from core.observability.mysql_repository import MySQLTelemetryRepository, _record_key


def test_observability_record_key_keeps_siblings_in_one_trace_distinct():
    trace = {"trace_id": "trace-1", "span_id": "span-1", "event_id": "event-1"}

    assert _record_key("trace", trace) == "trace-1"
    assert _record_key("span", trace) == "span-1"
    assert _record_key("event", trace) == "event-1"
    assert _record_key("span", {**trace, "span_id": "span-2"}) != _record_key("span", trace)


def test_mysql_rows_applies_filters_and_window_in_sql():
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Connection:
        def __init__(self):
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    class Connect:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return Connect(self.connection)

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()
    repository._rows(
        "trace",
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        trace_id="trace-1",
        limit=12,
        offset=24,
    )

    sql = str(repository._engine.connection.statement.compile(dialect=mysql.dialect()))
    assert "trace_id" in sql
    assert "created_at" in sql
    assert "LIMIT %s, %s" in sql


def test_mysql_rows_denies_an_empty_workspace_scope_in_sql():
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Connection:
        def __init__(self):
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    class Connect:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return Connect(self.connection)

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()
    repository._rows("trace", workspace_ids=frozenset())

    sql = str(repository._engine.connection.statement.compile(dialect=mysql.dialect()))
    assert "false" in sql.lower()


def test_prune_deletes_expired_trace_span_and_event_rows():
    class Result:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class Connection:
        def __init__(self):
            self.statements = []
            self.rowcounts = iter((4, 3, 2))

        def execute(self, statement):
            self.statements.append(statement)
            return Result(next(self.rowcounts))

    class Begin:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def begin(self):
            return Begin(self.connection)

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()

    assert repository.prune(trace_days=30, event_days=14) == {
        "traces": 2,
        "spans": 4,
        "events": 3,
        "daily_metrics": 0,
    }
    assert len(repository._engine.connection.statements) == 3
    assert "expired_trace_ids" in str(repository._engine.connection.statements[0])
    assert "expired_trace_ids" in str(repository._engine.connection.statements[1])


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

    calls = []

    def fake_rows(kind, **kwargs):
        calls.append((kind, kwargs))
        return rows if kind == "span" else []

    monkeypatch.setattr(repository, "_rows", fake_rows)

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
    assert calls[0][0] == "span"
    assert calls[0][1]["since"].tzinfo == timezone.utc
