"""Cross-process notifications for durable quota snapshot changes."""

from __future__ import annotations

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)
DEFAULT_QUOTA_SNAPSHOT_CHANNEL = "nlp-agent:quota-snapshot"


def snapshot_message(
    *, owner_type: str | None = None, owner_id: str | None = None
) -> str:
    return json.dumps(
        {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "refresh_required": True,
        },
        separators=(",", ":"),
    )


class QuotaSnapshotRedisPublisher:
    """Synchronous Redis publisher used from quota's database worker threads.

    Quota writes already run in worker threads.  A separate synchronous Redis
    connection keeps notification delivery independent from the event loop and
    makes the same publisher usable by Web, Worker, and the expiry reaper.
    Notification failure is logged but never rolls back accounting.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        channel: str = DEFAULT_QUOTA_SNAPSHOT_CHANNEL,
        client: Any | None = None,
    ) -> None:
        self.channel = channel
        if client is not None:
            self._client = client
        elif redis_url.strip():
            from redis import Redis

            self._client = Redis.from_url(redis_url, decode_responses=True)
        else:
            self._client = None

    def publish(
        self, *, owner_type: str | None = None, owner_id: str | None = None
    ) -> bool:
        if self._client is None:
            return False
        try:
            self._client.publish(
                self.channel,
                snapshot_message(owner_type=owner_type, owner_id=owner_id),
            )
            return True
        except Exception:
            logger.exception("quota snapshot notification publish failed")
            return False

    def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            close()
