"""Read free academic-search operational counters from Redis."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json

from core.tool_config import load_agent_runtime_config
from server.tools.academic.runtime import create_academic_redis_store


async def run(day: str | None) -> int:
    config = load_agent_runtime_config().tools.academic.reliability
    store = create_academic_redis_store(config)
    if store is None:
        print(json.dumps({"status": "disabled", "reason": "Redis URL is not configured"}))
        return 1
    try:
        counters = await store.daily_metrics(day)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "error_type": type(error).__name__,
                }
            )
        )
        return 2
    finally:
        with suppress(Exception):
            await store.close()

    output: dict[str, object] = {"status": "ok", "day": day or "today", **counters}
    searches = counters.get("searches", 0)
    output["cache_hit_rate"] = (
        counters.get("cache_hits", 0) / searches if searches else 0.0
    )
    providers = {
        key.split(":")[1]
        for key in counters
        if key.startswith("provider:") and len(key.split(":")) >= 3
    }
    output["providers"] = {
        provider: {
            "requests": requests,
            "average_latency_ms": (
                counters.get(f"provider:{provider}:latency_ms", 0) / requests
                if requests
                else 0.0
            ),
            "failures": sum(
                value
                for key, value in counters.items()
                if key.startswith(f"provider:{provider}:status:")
                and not key.endswith(":ok")
            ),
        }
        for provider in sorted(providers)
        for requests in [counters.get(f"provider:{provider}:requests", 0)]
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Read academic search Redis metrics")
    parser.add_argument("--day", help="UTC date in YYYY-MM-DD; defaults to today")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.day)))


if __name__ == "__main__":
    main()
