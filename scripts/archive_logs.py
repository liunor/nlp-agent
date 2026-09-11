"""Export structured application logs into one file per day.

The runtime already stores logs by date and process. This command is kept as
an optional export for offline handoff or long-term archival.

Usage:
    python scripts/archive_logs.py
    python scripts/archive_logs.py --log-dir /app/logs --output-dir /backup/nova-logs
    python scripts/archive_logs.py --clear
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.logs import collect_records


def archive(log_dir: Path, output_dir: Path, *, clear: bool = False) -> int:
    records = collect_records(log_dir)
    if not records:
        print(f"[skip] no JSONL records found under {log_dir}")
        return 0

    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        day = str(record.get("timestamp", ""))[:10]
        if len(day) == 10:
            buckets[day].append(record)

    written = 0
    for day, day_records in sorted(buckets.items()):
        day_dir = output_dir / day
        day_dir.mkdir(parents=True, exist_ok=True)
        for name, selected in (
            ("all.jsonl", day_records),
            ("error.jsonl", [item for item in day_records if item.get("level") == "error"]),
        ):
            if not selected:
                continue
            target = day_dir / name
            target.write_text(
                "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in selected),
                encoding="utf-8",
            )
            written += len(selected)

    print(f"records={len(records)} archived={written} output={output_dir}")
    if clear:
        cleared = 0
        for path in log_dir.rglob("*.log"):
            try:
                path.write_text("", encoding="utf-8")
                cleared += 1
            except OSError as error:
                print(f"[skip] could not clear {path}: {error}")
        print(f"cleared={cleared}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive NLP Agent JSONL logs")
    parser.add_argument("--log-dir", default=os.getenv("NLP_AGENT_LOG_DIR", "logs"))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--clear", action="store_true", help="clear source .log files after export")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    output_dir = Path(args.output_dir) if args.output_dir else log_dir / "archive"
    archive(log_dir, output_dir, clear=args.clear)


if __name__ == "__main__":
    main()
