import json
import logging
from datetime import datetime, timedelta

from scripts.logs import collect_records
from utils.daily_handler import DailyDirectoryHandler
from utils.logger import logging_options


def test_logging_options_are_configurable_for_container_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("NLP_AGENT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("NLP_AGENT_LOG_SERVICE", "nova-web")
    monkeypatch.setenv("NLP_AGENT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NLP_AGENT_LOG_STDOUT", "true")
    monkeypatch.setenv("NLP_AGENT_LOG_RETENTION_DAYS", "14")

    options = logging_options()

    assert options.base_dir == tmp_path / "logs" / "nova-web"
    assert options.level == logging.DEBUG
    assert options.stdout is True
    assert options.retention_days == 14


def test_daily_handler_uses_unique_process_directory_and_removes_expired_days(tmp_path):
    old_day = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    expired = tmp_path / "logs" / old_day
    expired.mkdir(parents=True)
    (expired / "old.log").write_text("old", encoding="utf-8")

    handler = DailyDirectoryHandler(
        base_dir=tmp_path / "logs",
        retention_days=7,
        process_id=1234,
    )
    try:
        handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None))
    finally:
        handler.close()

    assert not expired.exists()
    assert handler.path.name == "all.log"
    assert handler.run_dir.name.endswith("-p1234")
    assert handler.path.read_text(encoding="utf-8").strip()


def test_log_query_filters_records_across_service_run_directories(tmp_path):
    log_path = tmp_path / "logs" / "nova-worker" / "2026-09-11" / "10-00-00-p9" / "all.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in (
                {"timestamp": "2026-09-11T02:00:00Z", "level": "info", "logger": "worker", "event": "started"},
                {"timestamp": "2026-09-11T02:01:00Z", "level": "error", "logger": "worker", "event": "failed", "trace_id": "trace-1"},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    records = collect_records(tmp_path / "logs", level="error", trace_id="trace-1")

    assert len(records) == 1
    assert records[0]["event"] == "failed"
