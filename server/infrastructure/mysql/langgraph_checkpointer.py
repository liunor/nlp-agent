"""Production MySQL saver for LangGraph's asynchronous checkpoint contract."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any
import uuid

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    WRITES_IDX_MAP,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import and_, delete, desc, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from .models import (
    LangGraphCheckpointBlobModel,
    LangGraphCheckpointModel,
    LangGraphCheckpointWriteModel,
)


class MySQLCheckpointSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints, channels and pending writes in MySQL.

    Payloads deliberately remain LangGraph-serde binary values: application schemas must not
    inspect or mutate checkpoint internals.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__()
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def adelete_thread(
        self,
        thread_id: str,
        *,
        workspace_id: str,
        user_id: str,
    ) -> None:
        """Delete only the caller-owned durable state for a session."""
        async with self._sessions.begin() as session:
            ownership = (
                LangGraphCheckpointModel.workspace_id == workspace_id,
                LangGraphCheckpointModel.owner_user_id == user_id,
            )
            await session.execute(
                delete(LangGraphCheckpointWriteModel).where(
                    LangGraphCheckpointWriteModel.thread_id == thread_id,
                    LangGraphCheckpointWriteModel.workspace_id == workspace_id,
                    LangGraphCheckpointWriteModel.owner_user_id == user_id,
                )
            )
            await session.execute(
                delete(LangGraphCheckpointBlobModel).where(
                    LangGraphCheckpointBlobModel.thread_id == thread_id,
                    LangGraphCheckpointBlobModel.workspace_id == workspace_id,
                    LangGraphCheckpointBlobModel.owner_user_id == user_id,
                )
            )
            await session.execute(
                delete(LangGraphCheckpointModel).where(
                    LangGraphCheckpointModel.thread_id == thread_id, *ownership
                )
            )

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        # LangGraph run ids are not persisted as a separate identity; callers should use
        # adelete_thread when removing a session's complete checkpoint history.
        return None

    def _unsupported_sync(self, operation: str) -> None:
        raise RuntimeError(f"{operation} is unavailable on async MySQLCheckpointSaver; use the async LangGraph API")

    def get_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        self._unsupported_sync("get_tuple")

    def list(self, config: dict[str, Any] | None, *, filter: dict[str, Any] | None = None, before: dict[str, Any] | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        self._unsupported_sync("list")
        yield from ()

    def put(self, config: dict[str, Any], checkpoint: dict[str, Any], metadata: dict[str, Any], new_versions: dict[str, Any]) -> dict[str, Any]:
        self._unsupported_sync("put")

    def put_writes(self, config: dict[str, Any], writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        self._unsupported_sync("put_writes")

    async def _tuple_from_row(self, row: LangGraphCheckpointModel) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint_blob))
        metadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
        async with self._sessions() as session:
            blobs = (await session.scalars(select(LangGraphCheckpointBlobModel).where(
                LangGraphCheckpointBlobModel.thread_id == row.thread_id,
                LangGraphCheckpointBlobModel.checkpoint_ns == row.checkpoint_ns,
                LangGraphCheckpointBlobModel.workspace_id == row.workspace_id,
                LangGraphCheckpointBlobModel.owner_user_id == row.owner_user_id,
            ))).all()
            writes = (await session.scalars(select(LangGraphCheckpointWriteModel).where(
                LangGraphCheckpointWriteModel.thread_id == row.thread_id,
                LangGraphCheckpointWriteModel.checkpoint_ns == row.checkpoint_ns,
                LangGraphCheckpointWriteModel.checkpoint_id == row.checkpoint_id,
                LangGraphCheckpointWriteModel.workspace_id == row.workspace_id,
                LangGraphCheckpointWriteModel.owner_user_id == row.owner_user_id,
            ).order_by(LangGraphCheckpointWriteModel.task_id, LangGraphCheckpointWriteModel.write_index))).all()
        values = {
            blob.channel: self.serde.loads_typed((blob.value_type, blob.value_blob))
            for blob in blobs
            if str(checkpoint["channel_versions"].get(blob.channel)) == blob.version
            and blob.value_type != "empty"
        }
        return CheckpointTuple(
            config={"configurable": {"thread_id": row.thread_id, "checkpoint_ns": row.checkpoint_ns, "checkpoint_id": row.checkpoint_id, "workspace_id": row.workspace_id, "user_id": row.owner_user_id}},
            checkpoint={**checkpoint, "channel_values": values},
            metadata=metadata,
            parent_config=({"configurable": {"thread_id": row.thread_id, "checkpoint_ns": row.checkpoint_ns, "checkpoint_id": row.parent_checkpoint_id, "workspace_id": row.workspace_id, "user_id": row.owner_user_id}} if row.parent_checkpoint_id else None),
            pending_writes=[
                (item.task_id, item.channel, self.serde.loads_typed((item.value_type, item.value_blob)))
                for item in writes
                if item.value_type != "empty"
            ],
        )

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        configured = config["configurable"]
        thread_id, namespace = configured["thread_id"], configured.get("checkpoint_ns", "")
        statement = select(LangGraphCheckpointModel).where(
            LangGraphCheckpointModel.thread_id == thread_id,
            LangGraphCheckpointModel.checkpoint_ns == namespace,
        )
        if configured.get("workspace_id") and configured.get("user_id"):
            statement = statement.where(
                LangGraphCheckpointModel.workspace_id == configured["workspace_id"],
                LangGraphCheckpointModel.owner_user_id == configured["user_id"],
            )
        else:
            raise PermissionError("checkpoint identity is required")
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            statement = statement.where(LangGraphCheckpointModel.checkpoint_id == checkpoint_id)
        else:
            statement = statement.order_by(desc(LangGraphCheckpointModel.checkpoint_id)).limit(1)
        async with self._sessions() as session:
            row = await session.scalar(statement)
        return await self._tuple_from_row(row) if row else None

    async def alist(self, config: dict[str, Any] | None, *, filter: dict[str, Any] | None = None, before: dict[str, Any] | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        statement = select(LangGraphCheckpointModel).order_by(desc(LangGraphCheckpointModel.checkpoint_id))
        if config:
            configured = config["configurable"]
            statement = statement.where(LangGraphCheckpointModel.thread_id == configured["thread_id"])
            if not configured.get("workspace_id") or not configured.get("user_id"):
                raise PermissionError("checkpoint identity is required")
            statement = statement.where(
                LangGraphCheckpointModel.workspace_id == configured["workspace_id"],
                LangGraphCheckpointModel.owner_user_id == configured["user_id"],
            )
            if "checkpoint_ns" in configured:
                statement = statement.where(LangGraphCheckpointModel.checkpoint_ns == configured["checkpoint_ns"])
            if checkpoint_id := get_checkpoint_id(config):
                statement = statement.where(LangGraphCheckpointModel.checkpoint_id == checkpoint_id)
        if before and (checkpoint_id := get_checkpoint_id(before)):
            statement = statement.where(LangGraphCheckpointModel.checkpoint_id < checkpoint_id)
        if limit is not None:
            statement = statement.limit(limit)
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        for row in rows:
            item = await self._tuple_from_row(row)
            if not filter or all(item.metadata.get(key) == value for key, value in filter.items()):
                yield item

    async def aput(self, config: dict[str, Any], checkpoint: dict[str, Any], metadata: dict[str, Any], new_versions: dict[str, Any]) -> dict[str, Any]:
        configured = config["configurable"]
        thread_id, namespace = configured["thread_id"], configured.get("checkpoint_ns", "")
        workspace_id, owner_user_id = configured.get("workspace_id"), configured.get("user_id")
        if not workspace_id or not owner_user_id:
            raise PermissionError("checkpoint identity is required")
        stored = checkpoint.copy()
        values = stored.pop("channel_values")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(stored)
        metadata_type, metadata_blob = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(LangGraphCheckpointModel)
                .where(
                    LangGraphCheckpointModel.thread_id == thread_id,
                    LangGraphCheckpointModel.checkpoint_ns == namespace,
                    LangGraphCheckpointModel.checkpoint_id == checkpoint["id"],
                )
                .with_for_update()
            )
            if existing is not None and (
                existing.workspace_id != workspace_id
                or existing.owner_user_id != owner_user_id
            ):
                raise PermissionError("checkpoint belongs to another principal")
            row = insert(LangGraphCheckpointModel).values(
                id=str(uuid.uuid4()), thread_id=thread_id, checkpoint_ns=namespace,
                workspace_id=workspace_id, owner_user_id=owner_user_id,
                checkpoint_id=checkpoint["id"], parent_checkpoint_id=configured.get("checkpoint_id"),
                checkpoint_type=checkpoint_type, checkpoint_blob=checkpoint_blob,
                metadata_type=metadata_type, metadata_blob=metadata_blob,
            ).on_duplicate_key_update(
                parent_checkpoint_id=configured.get("checkpoint_id"), checkpoint_type=checkpoint_type,
                checkpoint_blob=checkpoint_blob, metadata_type=metadata_type, metadata_blob=metadata_blob,
            )
            await session.execute(row)
            for channel, version in new_versions.items():
                value_type, value_blob = self.serde.dumps_typed(values[channel]) if channel in values else ("empty", b"")
                existing_blob = await session.scalar(
                    select(LangGraphCheckpointBlobModel)
                    .where(
                        LangGraphCheckpointBlobModel.thread_id == thread_id,
                        LangGraphCheckpointBlobModel.checkpoint_ns == namespace,
                        LangGraphCheckpointBlobModel.channel == channel,
                        LangGraphCheckpointBlobModel.version == str(version),
                    )
                    .with_for_update()
                )
                if existing_blob is not None and (
                    existing_blob.workspace_id != workspace_id
                    or existing_blob.owner_user_id != owner_user_id
                ):
                    raise PermissionError("checkpoint blob belongs to another principal")
                blob = insert(LangGraphCheckpointBlobModel).values(
                    id=str(uuid.uuid4()), thread_id=thread_id, workspace_id=workspace_id,
                    owner_user_id=owner_user_id, checkpoint_ns=namespace, channel=channel,
                    version=str(version), value_type=value_type, value_blob=value_blob,
                ).on_duplicate_key_update(value_type=value_type, value_blob=value_blob)
                await session.execute(blob)
        return {"configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": namespace,
            "checkpoint_id": checkpoint["id"],
            "workspace_id": workspace_id,
            "user_id": owner_user_id,
        }}

    async def aput_writes(self, config: dict[str, Any], writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        configured = config["configurable"]
        thread_id, namespace, checkpoint_id = configured["thread_id"], configured.get("checkpoint_ns", ""), configured["checkpoint_id"]
        workspace_id, owner_user_id = configured.get("workspace_id"), configured.get("user_id")
        if not workspace_id or not owner_user_id:
            raise PermissionError("checkpoint identity is required")
        async with self._sessions.begin() as session:
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                value_type, value_blob = self.serde.dumps_typed(value)
                existing_write = await session.scalar(
                    select(LangGraphCheckpointWriteModel)
                    .where(
                        LangGraphCheckpointWriteModel.thread_id == thread_id,
                        LangGraphCheckpointWriteModel.checkpoint_ns == namespace,
                        LangGraphCheckpointWriteModel.checkpoint_id == checkpoint_id,
                        LangGraphCheckpointWriteModel.task_id == task_id,
                        LangGraphCheckpointWriteModel.write_index == write_index,
                    )
                    .with_for_update()
                )
                if existing_write is not None and (
                    existing_write.workspace_id != workspace_id
                    or existing_write.owner_user_id != owner_user_id
                ):
                    raise PermissionError("checkpoint write belongs to another principal")
                statement = insert(LangGraphCheckpointWriteModel).values(
                    id=str(uuid.uuid4()), thread_id=thread_id, workspace_id=workspace_id,
                    owner_user_id=owner_user_id, checkpoint_ns=namespace,
                    checkpoint_id=checkpoint_id, task_id=task_id, write_index=write_index,
                    channel=channel, value_type=value_type, value_blob=value_blob, task_path=task_path,
                )
                if write_index < 0:
                    statement = statement.on_duplicate_key_update(value_type=value_type, value_blob=value_blob, task_path=task_path)
                else:
                    statement = statement.prefix_with("IGNORE")
                await session.execute(statement)
