"""Production MySQL saver for LangGraph's asynchronous checkpoint contract."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from typing import Any, TypeVar

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    WRITES_IDX_MAP,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import and_, delete, desc, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import (
    ConversationModel,
    LangGraphCheckpointBlobModel,
    LangGraphCheckpointModel,
    LangGraphCheckpointWriteModel,
)


_T = TypeVar("_T")
_RETRYABLE_MYSQL_TRANSACTION_ERRORS = frozenset({1205, 1213})
_MYSQL_TRANSACTION_ATTEMPTS = 3
_MYSQL_TRANSACTION_RETRY_BASE_S = 0.02


def _mysql_error_code(error: DBAPIError) -> int | None:
    args = getattr(getattr(error, "orig", None), "args", ())
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


async def _retry_mysql_transaction(
    operation: Callable[[], Awaitable[_T]],
) -> _T:
    """Retry only complete transactions rejected by MySQL lock arbitration."""

    for attempt in range(_MYSQL_TRANSACTION_ATTEMPTS):
        try:
            return await operation()
        except DBAPIError as error:
            retryable = (
                not error.connection_invalidated
                and _mysql_error_code(error) in _RETRYABLE_MYSQL_TRANSACTION_ERRORS
            )
            if not retryable or attempt + 1 >= _MYSQL_TRANSACTION_ATTEMPTS:
                raise
            await asyncio.sleep(_MYSQL_TRANSACTION_RETRY_BASE_S * (2**attempt))
    raise RuntimeError("unreachable MySQL checkpoint retry state")


class MySQLCheckpointSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints, channels and pending writes in MySQL.

    Payloads deliberately remain LangGraph-serde binary values: application schemas must not
    inspect or mutate checkpoint internals.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__()
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _require_owner(
        row: Any,
        *,
        workspace_id: str,
        owner_user_id: str,
        record_name: str,
    ) -> None:
        if row.workspace_id != workspace_id or row.owner_user_id != owner_user_id:
            raise PermissionError(f"{record_name} belongs to another principal")

    async def _require_thread_owner(
        self,
        session: AsyncSession,
        *,
        thread_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> None:
        conversation = await session.get(ConversationModel, thread_id)
        if conversation is None:
            raise PermissionError("checkpoint conversation does not exist")
        self._require_owner(
            conversation,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            record_name="checkpoint conversation",
        )

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

        async def delete_once() -> None:
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

        await _retry_mysql_transaction(delete_once)

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

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        configured = config["configurable"]
        thread_id, namespace = (
            configured["thread_id"],
            configured.get("checkpoint_ns", ""),
        )
        workspace_id, owner_user_id = (
            configured.get("workspace_id"),
            configured.get("user_id"),
        )
        if not workspace_id or not owner_user_id:
            raise PermissionError("checkpoint identity is required")
        stored = checkpoint.copy()
        values = stored.pop("channel_values")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(stored)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )

        async def put_once() -> None:
            async with self._sessions.begin() as session:
                await self._require_thread_owner(
                    session,
                    thread_id=thread_id,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                )
                checkpoint_claim = (
                    insert(LangGraphCheckpointModel)
                    .values(
                        id=str(uuid.uuid4()),
                        thread_id=thread_id,
                        checkpoint_ns=namespace,
                        workspace_id=workspace_id,
                        owner_user_id=owner_user_id,
                        checkpoint_id=checkpoint["id"],
                        parent_checkpoint_id=configured.get("checkpoint_id"),
                        checkpoint_type=checkpoint_type,
                        checkpoint_blob=checkpoint_blob,
                        metadata_type=metadata_type,
                        metadata_blob=metadata_blob,
                    )
                    .on_duplicate_key_update(
                        id=LangGraphCheckpointModel.id,
                    )
                )
                await session.execute(checkpoint_claim)
                checkpoint_row = await session.scalar(
                    select(LangGraphCheckpointModel)
                    .where(
                        LangGraphCheckpointModel.thread_id == thread_id,
                        LangGraphCheckpointModel.checkpoint_ns == namespace,
                        LangGraphCheckpointModel.checkpoint_id == checkpoint["id"],
                    )
                    .with_for_update()
                )
                if checkpoint_row is None:
                    raise RuntimeError("checkpoint claim did not create a row")
                self._require_owner(
                    checkpoint_row,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    record_name="checkpoint",
                )
                checkpoint_row.parent_checkpoint_id = configured.get("checkpoint_id")
                checkpoint_row.checkpoint_type = checkpoint_type
                checkpoint_row.checkpoint_blob = checkpoint_blob
                checkpoint_row.metadata_type = metadata_type
                checkpoint_row.metadata_blob = metadata_blob
                for channel in sorted(new_versions):
                    version = new_versions[channel]
                    value_type, value_blob = (
                        self.serde.dumps_typed(values[channel])
                        if channel in values
                        else ("empty", b"")
                    )
                    blob_claim = (
                        insert(LangGraphCheckpointBlobModel)
                        .values(
                            id=str(uuid.uuid4()),
                            thread_id=thread_id,
                            workspace_id=workspace_id,
                            owner_user_id=owner_user_id,
                            checkpoint_ns=namespace,
                            channel=channel,
                            version=str(version),
                            value_type=value_type,
                            value_blob=value_blob,
                        )
                        .on_duplicate_key_update(id=LangGraphCheckpointBlobModel.id)
                    )
                    await session.execute(blob_claim)
                    blob_row = await session.scalar(
                        select(LangGraphCheckpointBlobModel)
                        .where(
                            LangGraphCheckpointBlobModel.thread_id == thread_id,
                            LangGraphCheckpointBlobModel.checkpoint_ns == namespace,
                            LangGraphCheckpointBlobModel.channel == channel,
                            LangGraphCheckpointBlobModel.version == str(version),
                        )
                        .with_for_update()
                    )
                    if blob_row is None:
                        raise RuntimeError("checkpoint blob claim did not create a row")
                    self._require_owner(
                        blob_row,
                        workspace_id=workspace_id,
                        owner_user_id=owner_user_id,
                        record_name="checkpoint blob",
                    )
                    blob_row.value_type = value_type
                    blob_row.value_blob = value_blob

        await _retry_mysql_transaction(put_once)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint["id"],
                "workspace_id": workspace_id,
                "user_id": owner_user_id,
            }
        }

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configured = config["configurable"]
        thread_id, namespace, checkpoint_id = (
            configured["thread_id"],
            configured.get("checkpoint_ns", ""),
            configured["checkpoint_id"],
        )
        workspace_id, owner_user_id = (
            configured.get("workspace_id"),
            configured.get("user_id"),
        )
        if not workspace_id or not owner_user_id:
            raise PermissionError("checkpoint identity is required")
        prepared = []
        for index, (channel, value) in enumerate(writes):
            write_index = WRITES_IDX_MAP.get(channel, index)
            value_type, value_blob = self.serde.dumps_typed(value)
            prepared.append((write_index, channel, value_type, value_blob))
        prepared.sort(key=lambda item: (item[0], item[1]))

        async def put_writes_once() -> None:
            async with self._sessions.begin() as session:
                await self._require_thread_owner(
                    session,
                    thread_id=thread_id,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                )
                for write_index, channel, value_type, value_blob in prepared:
                    claim = (
                        insert(LangGraphCheckpointWriteModel)
                        .values(
                            id=str(uuid.uuid4()),
                            thread_id=thread_id,
                            workspace_id=workspace_id,
                            owner_user_id=owner_user_id,
                            checkpoint_ns=namespace,
                            checkpoint_id=checkpoint_id,
                            task_id=task_id,
                            write_index=write_index,
                            channel=channel,
                            value_type=value_type,
                            value_blob=value_blob,
                            task_path=task_path,
                        )
                        .on_duplicate_key_update(id=LangGraphCheckpointWriteModel.id)
                    )
                    await session.execute(claim)
                    write_row = await session.scalar(
                        select(LangGraphCheckpointWriteModel)
                        .where(
                            LangGraphCheckpointWriteModel.thread_id == thread_id,
                            LangGraphCheckpointWriteModel.checkpoint_ns == namespace,
                            LangGraphCheckpointWriteModel.checkpoint_id
                            == checkpoint_id,
                            LangGraphCheckpointWriteModel.task_id == task_id,
                            LangGraphCheckpointWriteModel.write_index == write_index,
                        )
                        .with_for_update()
                    )
                    if write_row is None:
                        raise RuntimeError(
                            "checkpoint write claim did not create a row"
                        )
                    self._require_owner(
                        write_row,
                        workspace_id=workspace_id,
                        owner_user_id=owner_user_id,
                        record_name="checkpoint write",
                    )
                    if write_index < 0:
                        write_row.value_type = value_type
                        write_row.value_blob = value_blob
                        write_row.task_path = task_path

        await _retry_mysql_transaction(put_writes_once)
