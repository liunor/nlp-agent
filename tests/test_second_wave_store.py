from unittest.mock import AsyncMock, MagicMock

import pytest

from server.infrastructure.mysql import second_wave
from server.infrastructure.mysql.second_wave import SecondWaveStore


@pytest.mark.asyncio
async def test_second_wave_store_creates_durable_rows_without_runtime_ddl(monkeypatch) -> None:
    class FakeQuota:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reserve(self, *args, **kwargs):
            return object()

        async def finalize(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(second_wave, "AsyncStorageQuota", FakeQuota)
    session = AsyncMock(); session.add = MagicMock(); session.flush = AsyncMock()
    store = SecondWaveStore()
    checkpoint = await store.save_checkpoint(session, session_id="s", checkpoint_ns="", checkpoint_id="c", checkpoint={"v": 1}, metadata={})
    transcript = await store.append_transcript(session, session_id="s", message_uuid="m", parent_uuid=None, message_type="user", role="user", content="hello")
    memory = await store.save_memory(session, user_id="u", workspace_id="w", scope="user", document_key="k", content={"x": 1})
    audit = await store.audit_tool(session, tool_name="time", status="succeeded", request={})
    config = await store.save_runtime_config(session, scope="global", revision=1, config={"x": 1})
    assert all(item.id for item in (checkpoint, transcript, memory, audit, config))
    session.flush.assert_awaited()
