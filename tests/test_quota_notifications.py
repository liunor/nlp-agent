from __future__ import annotations

import json

from server.quota.notifications import QuotaSnapshotRedisPublisher


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.calls.append((channel, message))


def test_quota_snapshot_publisher_emits_scoped_cross_process_message():
    redis = FakeRedis()
    publisher = QuotaSnapshotRedisPublisher(
        "",
        channel="quota-snapshots",
        client=redis,
    )

    assert publisher.publish(owner_type="workspace", owner_id="workspace-1") is True
    assert redis.calls == [
        (
            "quota-snapshots",
            json.dumps(
                {
                    "owner_type": "workspace",
                    "owner_id": "workspace-1",
                    "refresh_required": True,
                },
                separators=(",", ":"),
            ),
        )
    ]
