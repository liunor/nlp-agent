"""Capacity acceptance checks live slot gauges, rather than only container state."""
import pytest

from scripts.verify_worker_capacity import matches, verify


def beats():
    return [{"worker_id": host + "-process", "version": 1, "concurrency": 16, "active_slots": 0}
            for host in ("first", "second")]


@pytest.mark.parametrize("change", ["old_profile", "one_worker", "duplicate", "stale_extra",
                                   "wrong_host", "invalid_slots", "malformed"])
def test_live_capacity_refuses_wrong_or_stale_heartbeats(change):
    values = beats()
    assert matches(values, ["first", "second"], 16)
    if change == "old_profile":
        values[1]["concurrency"] = 4
    elif change == "one_worker":
        values.pop()
    elif change == "duplicate":
        values[1] = values[0]
    elif change == "stale_extra":
        values.append({**values[0], "worker_id": "old-process"})
    elif change == "wrong_host":
        values[1]["worker_id"] = "unrelated-process"
    elif change == "invalid_slots":
        values[1]["active_slots"] = 17
    else:
        values[1] = None
    assert not matches(values, ["first", "second"], 16)


def test_acceptance_waits_for_replaced_workers_and_only_reads_redis():
    import json

    class Client:
        round = 0

        def scan_iter(self, **kwargs):
            self.round += 1
            return ["a", "b"]

        def get(self, key):
            if self.round == 1:
                return json.dumps({**beats()[0], "worker_id": "old-process"})
            return json.dumps(beats()[0 if key == "a" else 1])

    times = iter([0, 0])
    client = Client()
    result = verify(client, ["first", "second"], 16, clock=lambda: next(times), sleep=lambda _: None)
    assert client.round == 2
    assert result == {"worker_replicas": 2, "concurrency_per_worker": 16,
                      "total_task_slots": 32, "actual_worker_heartbeats_verified": True}


def test_acceptance_times_out_without_two_verified_workers():
    class Empty:
        def scan_iter(self, **kwargs):
            return []

    with pytest.raises(ValueError, match="EXPECTED_WORKER_HEARTBEATS"):
        verify(Empty(), ["first", "second"], 16, timeout=0)
