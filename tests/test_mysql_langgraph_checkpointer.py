import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from server.infrastructure.mysql.config import DatabaseConfig
from server.infrastructure.mysql.engine import create_engine
from server.infrastructure.mysql.langgraph_checkpointer import (
    MySQLCheckpointSaver,
    _retry_mysql_transaction,
)


@pytest.mark.asyncio
async def test_mysql_transaction_retries_deadlocks_from_a_fresh_transaction():
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError(
                "INSERT",
                {},
                RuntimeError(1213, "Deadlock found when trying to get lock"),
            )
        return "stored"

    assert await _retry_mysql_transaction(operation) == "stored"
    assert attempts == 3


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
    other_workspace_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": "", "workspace_id": workspace_id, "user_id": user_id}}
    checkpoint = {
        "v": 1, "id": "00000000-0000-0000-0000-000000000001", "ts": "2026-08-01T00:00:00Z",
        "channel_values": {"messages": ["hello"]}, "channel_versions": {"messages": "1"},
        "versions_seen": {}, "updated_channels": ["messages"],
    }
    async with engine.begin() as connection:
        for current_workspace_id in (workspace_id, other_workspace_id):
            await connection.execute(
                text(
                    "INSERT INTO nlp_workspaces (id, slug, name, status) "
                    "VALUES (:id, :slug, :name, 'active')"
                ),
                {
                    "id": current_workspace_id,
                    "slug": f"checkpoint-{current_workspace_id[:12]}",
                    "name": "Checkpoint test workspace",
                },
            )
        for current_user_id in (user_id, other_user_id):
            await connection.execute(
                text(
                    "INSERT INTO nlp_users "
                    "(id, username, password_hash, display_name, status) "
                    "VALUES (:id, :username, :password_hash, :display_name, 'active')"
                ),
                {
                    "id": current_user_id,
                    "username": current_user_id,
                    "password_hash": "test-only-not-a-login-hash",
                    "display_name": "Checkpoint test user",
                },
            )
        await connection.execute(
            text(
                "INSERT INTO nlp_conversations "
                "(id, workspace_id, owner_user_id, title) "
                "VALUES (:id, :workspace_id, :owner_user_id, :title)"
            ),
            {
                "id": thread_id,
                "workspace_id": workspace_id,
                "owner_user_id": user_id,
                "title": "Checkpoint integration test",
            },
        )

    try:
        stored = await saver.aput(config, checkpoint, {"source": "input", "step": 1}, {"messages": "1"})
        await saver.aput_writes(stored, [("tasks", {"state": "pending"})], "task-1")
        await saver.aput_writes(stored, [("tasks", {"state": "duplicate"})], "task-1")
        await asyncio.gather(
            *(
                saver.aput_writes(
                    stored,
                    [
                        ("first", {"task": index, "slot": 0}),
                        ("second", {"task": index, "slot": 1}),
                    ],
                    f"parallel-{index:02d}",
                )
                for index in range(24)
            )
        )
        await saver.aput_writes(stored, [("__error__", {"attempt": 1})], "task-error")
        await saver.aput_writes(stored, [("__error__", {"attempt": 2})], "task-error")

        conflicting = {
            "configurable": {
                **stored["configurable"],
                "workspace_id": other_workspace_id,
                "user_id": other_user_id,
            }
        }
        with pytest.raises(PermissionError, match="checkpoint conversation"):
            await saver.aput_writes(
                conflicting,
                [("tasks", {"state": "intruder"})],
                "fresh-intruder-task",
            )
        conflicting_checkpoint = {
            **checkpoint,
            "id": "00000000-0000-0000-0000-000000000002",
        }
        with pytest.raises(PermissionError, match="checkpoint conversation"):
            await saver.aput(
                conflicting,
                conflicting_checkpoint,
                {"source": "input", "step": 2},
                {"messages": "1"},
            )

        restored = await saver.aget_tuple(stored)
        assert restored is not None
        assert restored.checkpoint["channel_values"] == {"messages": ["hello"]}
        assert restored.metadata["source"] == "input"
        pending = {
            (task_id, channel): value
            for task_id, channel, value in restored.pending_writes
        }
        assert pending[("task-1", "tasks")] == {"state": "pending"}
        assert pending[("task-error", "__error__")] == {"attempt": 2}
        for index in range(24):
            task_id = f"parallel-{index:02d}"
            assert pending[(task_id, "first")] == {"task": index, "slot": 0}
            assert pending[(task_id, "second")] == {"task": index, "slot": 1}
        assert len(pending) == 50
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoint_writes WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoint_blobs WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(text("DELETE FROM nlp_langgraph_checkpoints WHERE thread_id = :thread_id"), {"thread_id": thread_id})
            await connection.execute(
                text("DELETE FROM nlp_conversations WHERE id = :thread_id"),
                {"thread_id": thread_id},
            )
            await connection.execute(
                text("DELETE FROM nlp_users WHERE id IN (:first_id, :second_id)"),
                {"first_id": user_id, "second_id": other_user_id},
            )
            await connection.execute(
                text("DELETE FROM nlp_workspaces WHERE id IN (:first_id, :second_id)"),
                {"first_id": workspace_id, "second_id": other_workspace_id},
            )
        await saver.aclose()
