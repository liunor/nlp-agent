import os
import os
import uuid

import pytest
from sqlalchemy import text

from server.infrastructure.mysql.config import DatabaseConfig
from server.infrastructure.mysql.engine import create_engine
from server.infrastructure.mysql.langgraph_checkpointer import MySQLCheckpointSaver


@pytest.mark.asyncio
async def test_mysql_checkpoint_round_trip_and_idempotent_writes():
    url = os.environ.get("NLP_AGENT_DATABASE_URL")
    if not url:
        pytest.skip("requires NLP_AGENT_DATABASE_URL")
    engine = create_engine(DatabaseConfig(url=url))
    saver = MySQLCheckpointSaver(engine)
    thread_id = f"test-{uuid.uuid4()}"
    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": "", "workspace_id": workspace_id, "user_id": user_id}}
    checkpoint = {
        "v": 1, "id": "00000000-0000-0000-0000-000000000001", "ts": "2026-08-01T00:00:00Z",
        "channel_values": {"messages": ["hello"]}, "channel_versions": {"messages": "1"},
        "versions_seen": {}, "updated_channels": ["messages"],
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO nlp_workspaces (id, slug, name, status) "
                "VALUES (:id, :slug, :name, 'active')"
            ),
            {"id": workspace_id, "slug": f"checkpoint-{workspace_id[:12]}", "name": "Checkpoint test workspace"},
        )
        await connection.execute(
            text(
                "INSERT INTO nlp_users "
                "(id, username, password_hash, display_name, status) "
                "VALUES (:id, :username, :password_hash, :display_name, 'active')"
            ),
            {
                "id": user_id,
                "username": user_id,
                "password_hash": "test-only-not-a-login-hash",
                "display_name": "Checkpoint test user",
            },
        )

    try:
        stored = await saver.aput(config, checkpoint, {"source": "input", "step": 1}, {"messages": "1"})
        await saver.aput_writes(stored, [("tasks", {"state": "pending"})], "task-1")
        await saver.aput_writes(stored, [("tasks", {"state": "duplicate"})], "task-1")
        restored = await saver.aget_tuple(stored)
        assert restored is not None
        assert restored.checkpoint["channel_values"] == {"messages": ["hello"]}
        assert restored.metadata["source"] == "input"
        assert restored.pending_writes == [("task-1", "tasks", {"state": "pending"})]
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoint_writes WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoint_blobs WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoints WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(text("DELETE FROM nlp_users WHERE id = :user_id"), {"user_id": user_id})
            await connection.execute(text("DELETE FROM nlp_workspaces WHERE id = :workspace_id"), {"workspace_id": workspace_id})
        await saver.aclose()
