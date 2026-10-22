"""Read TTL-backed Worker heartbeats; never submit tasks or change Redis keys."""
from __future__ import annotations

import argparse
import json
import os
import time


def matches(heartbeats, hostnames, concurrency):
    if len(heartbeats) != len(hostnames) or len(set(hostnames)) != len(hostnames):
        return False
    matched = set()
    for value in heartbeats:
        if not isinstance(value, dict):
            return False
        worker_id = value.get("worker_id")
        if (not isinstance(worker_id, str) or value.get("version") != 1
                or type(value.get("concurrency")) is not int or value["concurrency"] != concurrency
                or type(value.get("active_slots")) is not int
                or not 0 <= value["active_slots"] <= concurrency):
            return False
        hosts = [host for host in hostnames if worker_id.startswith(host + "-")]
        if len(hosts) != 1 or hosts[0] in matched:
            return False
        matched.add(hosts[0])
    return matched == set(hostnames)


def verify(client, hostnames, concurrency, *, timeout=45, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        values = []
        for key in client.scan_iter(match="nlp-agent:workers:heartbeat:*", count=100):
            raw = client.get(key)
            if raw is None:
                continue
            try:
                values.append(json.loads(raw))
            except (ValueError, TypeError):
                values.append(None)
            if len(values) > 100:
                raise ValueError("WORKER_HEARTBEAT_COUNT_REQUIRES_REVIEW")
        if matches(values, hostnames, concurrency):
            return {"worker_replicas": len(hostnames), "concurrency_per_worker": concurrency,
                    "total_task_slots": len(hostnames) * concurrency,
                    "actual_worker_heartbeats_verified": True}
        if clock() >= deadline:
            raise ValueError("EXPECTED_WORKER_HEARTBEATS_NOT_OBSERVED")
        sleep(2)


def main():
    from redis import Redis

    parser = argparse.ArgumentParser()
    parser.add_argument("--hostnames", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    args = parser.parse_args()
    hosts = json.loads(args.hostnames)
    if (not isinstance(hosts, list) or not hosts or len(hosts) > 2
            or any(not isinstance(host, str) or not host for host in hosts)
            or len(set(hosts)) != len(hosts) or not 1 <= args.concurrency <= 32):
        raise ValueError("INVALID_WORKER_CAPACITY_EXPECTATION")
    with Redis.from_url(os.environ["NLP_AGENT_REDIS_URL"], socket_connect_timeout=3,
                        socket_timeout=3) as client:
        print(json.dumps({"status": "ACTUAL_WORKER_CAPACITY_VERIFIED",
                          **verify(client, hosts, args.concurrency)}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never print Redis URLs, credentials or raw heartbeat identities.
        print(json.dumps({"status": "ACTUAL_WORKER_CAPACITY_CHECK_FAILED"}))
        raise SystemExit(1)
