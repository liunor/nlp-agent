"""查询应用 JSONL 日志。

示例:
    python scripts/logs.py --service nova-web --level error
    python scripts/logs.py --service nova-worker --trace-id TRACE_ID --tail 50
    python scripts/logs.py --json --since 2026-09-11T00:00:00Z
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable


def _iter_log_paths(log_dir: Path, service: str | None) -> Iterable[Path]:
    root = log_dir / service if service else log_dir
    if not root.is_dir():
        return ()
    # all.log is the canonical source. warning.log/error.log are subsets.
    return sorted(root.rglob("all.log"))


def collect_records(
    log_dir: str | os.PathLike[str] = "logs",
    *,
    service: str | None = None,
    level: str | None = None,
    logger_name: str | None = None,
    trace_id: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return matching records in chronological order across all runs."""
    records: list[dict[str, Any]] = []
    level = level.lower() if level else None
    for path in _iter_log_paths(Path(log_dir), service):
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if level and str(record.get("level", "")).lower() != level:
                    continue
                if logger_name and record.get("logger") != logger_name:
                    continue
                if trace_id and record.get("trace_id") != trace_id:
                    continue
                if since and str(record.get("timestamp", "")) < since:
                    continue
                records.append(record)

    records.sort(key=lambda item: str(item.get("timestamp", "")))
    if limit is None:
        return records
    return records[-limit:] if limit > 0 else []


def _render_human(record: dict[str, Any]) -> str:
    timestamp = record.get("timestamp", "-")
    level = str(record.get("level", "-")).upper()
    logger_name = record.get("logger", "-")
    event = str(record.get("event", "-")).replace("\n", " ")
    context = " ".join(
        f"{key}={record[key]}"
        for key in ("trace_id", "request_id", "session_id", "turn_id", "worker_id")
        if record.get(key)
    )
    return f"{timestamp} {level:<7} {logger_name} {event}" + (f" [{context}]" if context else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Query NLP Agent application logs")
    parser.add_argument("--log-dir", default=os.getenv("NLP_AGENT_LOG_DIR", "logs"))
    parser.add_argument("--service", help="service subdirectory, e.g. nova-web")
    parser.add_argument("--level", help="exact level: debug/info/warning/error")
    parser.add_argument("--logger", dest="logger_name")
    parser.add_argument("--trace-id")
    parser.add_argument("--since", help="inclusive ISO-8601 timestamp")
    parser.add_argument("--tail", type=int, default=100, help="number of records to show")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    records = collect_records(
        args.log_dir,
        service=args.service,
        level=args.level,
        logger_name=args.logger_name,
        trace_id=args.trace_id,
        since=args.since,
        limit=max(0, args.tail),
    )
    if args.as_json:
        print(json.dumps(records, ensure_ascii=False, indent=2, default=str))
    else:
        for record in records:
            print(_render_human(record))


if __name__ == "__main__":
    main()
