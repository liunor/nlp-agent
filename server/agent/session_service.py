"""Authenticated AgentSession ports for production and legacy test adapters."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission, authorization_service
from core.session_context import SessionContext, local_context_repository
from server.agent.session_storage import (
    CHAT_HISTORY_DIR,
    _load_sessions_index,
    _save_sessions_index,
    get_session_transcript_path,
)
from server.agent.node.session_storage import DATA_DIR
from server.infrastructure.mysql.models import (
    AgentCheckpointModel,
    ConversationMessageModel,
    ConversationModel,
    ConversationTranscriptModel,
    ExerciseSessionModel,
    GuidedSessionModel,
    LangGraphCheckpointBlobModel,
    LangGraphCheckpointModel,
    LangGraphCheckpointWriteModel,
    MemoryArchiveModel,
    ObservabilityRecordModel,
    ToolAuditModel,
    ToolCallModel,
    TurnCancellationModel,
    TurnEventModel,
    TurnModel,
)


class DatabaseSessionService:
    """MySQL-backed AgentSession port used by production gateways.

    ``ConversationModel`` is the durable AgentSession aggregate.  The local
    JSON index remains available only for explicitly injected legacy adapters;
    production session ownership and lifecycle are resolved from MySQL.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _context(row: ConversationModel) -> SessionContext:
        return SessionContext(
            session_id=row.id,
            user_id=row.owner_user_id,
            workspace_id=row.workspace_id,
            channel=row.channel,
        )

    @staticmethod
    def _summary(row: ConversationModel) -> dict[str, Any]:
        return {
            "session_id": row.id,
            "created_at": row.created_at,
            "last_active": row.last_message_at or row.updated_at or row.created_at,
            "user_id": row.owner_user_id,
            "workspace_id": row.workspace_id,
            "channel": row.channel,
        }

    @staticmethod
    def _scope(principal: AuthenticatedPrincipal):
        statement = select(ConversationModel).where(
            ConversationModel.owner_user_id == principal.user_id
        )
        if "*" not in principal.workspace_ids:
            statement = statement.where(
                ConversationModel.workspace_id.in_(principal.workspace_ids)
            )
        return statement

    async def create(
        self,
        principal: AuthenticatedPrincipal,
        *,
        workspace_id: str = "default",
        channel: str = "web",
    ) -> SessionContext:
        authorization_service.require(
            principal, Permission.AGENT_SESSION_CREATE, workspace_id=workspace_id
        )
        principal.require_workspace(workspace_id)
        context = SessionContext.create(
            user_id=principal.user_id,
            workspace_id=workspace_id,
            channel=channel,
        )
        async with self._sessions.begin() as session:
            session.add(
                ConversationModel(
                    id=context.session_id,
                    workspace_id=workspace_id,
                    owner_user_id=principal.user_id,
                    channel=channel,
                )
            )
        return context

    async def resolve(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == session_id,
                    ConversationModel.owner_user_id == principal.user_id,
                    ConversationModel.status == "active",
                )
            )
        if row is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        context = self._context(row)
        authorization_service.require(
            principal, Permission.AGENT_SESSION_READ, workspace_id=context.workspace_id
        )
        principal.require_context(context)
        return context

    async def list(self, principal: AuthenticatedPrincipal) -> list[dict[str, Any]]:
        return (await self.list_page(principal, limit=200))["items"]

    async def list_page(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        statement = self._scope(principal).where(ConversationModel.status == "active")
        page_limit = max(1, min(limit, 200))
        page_offset = max(0, offset)
        async with self._sessions() as session:
            total = int(
                await session.scalar(
                    select(func.count()).select_from(statement.order_by(None).subquery())
                )
                or 0
            )
            rows = list(
                (
                    await session.scalars(
                        statement.order_by(
                            ConversationModel.last_message_at.desc(),
                            ConversationModel.created_at.desc(),
                            ConversationModel.id.desc(),
                        )
                        .offset(page_offset)
                        .limit(page_limit)
                    )
                ).all()
            )
        return {
            "items": [self._summary(row) for row in rows],
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(rows) < total,
        }

    async def stats(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        scope = self._scope(principal)
        active = scope.where(ConversationModel.status == "active")
        scope_ids = scope.with_only_columns(ConversationModel.id).order_by(None)
        turn_scope = select(TurnModel).where(
            TurnModel.conversation_id.in_(scope_ids)
        )
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        async with self._sessions() as session:
            sessions_total = int(
                await session.scalar(
                    select(func.count()).select_from(scope.order_by(None).subquery())
                )
                or 0
            )
            sessions_active = int(
                await session.scalar(
                    select(func.count()).select_from(active.order_by(None).subquery())
                )
                or 0
            )
            turns_total = int(
                await session.scalar(
                    select(func.count()).select_from(turn_scope.order_by(None).subquery())
                )
                or 0
            )
            turns_last_24h = int(
                await session.scalar(
                    select(func.count()).select_from(
                        turn_scope.where(TurnModel.created_at >= since)
                        .order_by(None)
                        .subquery()
                    )
                )
                or 0
            )
            last_activity = await session.scalar(
                select(func.max(func.coalesce(
                    ConversationModel.last_message_at,
                    ConversationModel.updated_at,
                    ConversationModel.created_at,
                ))).where(
                    ConversationModel.id.in_(scope_ids)
                )
            )
        return {
            "sessions_total": sessions_total,
            "sessions_active": sessions_active,
            "turns_total": turns_total,
            "turns_last_24h": turns_last_24h,
            "last_activity_at": last_activity,
        }

    async def messages(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> list[dict[str, Any]]:
        await self.resolve(principal, session_id)
        async with self._sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ConversationTranscriptModel)
                        .where(ConversationTranscriptModel.session_id == session_id)
                        .order_by(ConversationTranscriptModel.created_at, ConversationTranscriptModel.id)
                    )
                ).all()
            )
        return [
            {
                "uuid": row.message_uuid,
                "parentUuid": row.parent_uuid,
                "sessionId": row.session_id,
                "timestamp": row.created_at.timestamp(),
                "type": row.message_type,
                "role": row.role,
                "content": row.content_json.get("content", row.content_json)
                if isinstance(row.content_json, dict)
                else row.content_json,
                "toolCalls": row.tool_json.get("tool_calls", [])
                if isinstance(row.tool_json, dict)
                else row.tool_json,
            }
            for row in rows
        ]

    async def touch(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        context = await self.resolve(principal, session_id)
        authorization_service.require(
            principal, Permission.AGENT_SESSION_UPDATE, workspace_id=context.workspace_id
        )
        async with self._sessions.begin() as session:
            await session.execute(
                update(ConversationModel)
                .where(ConversationModel.id == session_id, ConversationModel.owner_user_id == principal.user_id)
                .values(last_message_at=func.utc_timestamp(6))
            )
        return context

    async def delete(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        context = await self.resolve(principal, session_id)
        authorization_service.require(
            principal, Permission.AGENT_SESSION_DELETE, workspace_id=context.workspace_id
        )
        async with self._sessions.begin() as session:
            turn_ids = select(TurnModel.id).where(TurnModel.conversation_id == session_id)
            for statement in (
                delete(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == session_id
                ),
                delete(ExerciseSessionModel).where(
                    ExerciseSessionModel.conversation_id == session_id
                ),
                delete(GuidedSessionModel).where(
                    GuidedSessionModel.conversation_id == session_id
                ),
                delete(ConversationTranscriptModel).where(
                    ConversationTranscriptModel.session_id == session_id
                ),
                delete(AgentCheckpointModel).where(
                    AgentCheckpointModel.session_id == session_id
                ),
                delete(MemoryArchiveModel).where(MemoryArchiveModel.session_id == session_id),
                delete(LangGraphCheckpointModel).where(
                    LangGraphCheckpointModel.thread_id == session_id
                ),
                delete(LangGraphCheckpointBlobModel).where(
                    LangGraphCheckpointBlobModel.thread_id == session_id
                ),
                delete(LangGraphCheckpointWriteModel).where(
                    LangGraphCheckpointWriteModel.thread_id == session_id
                ),
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.session_id == session_id
                ),
                delete(ToolAuditModel).where(ToolAuditModel.turn_id.in_(turn_ids)),
                delete(ToolCallModel).where(ToolCallModel.turn_id.in_(turn_ids)),
                delete(TurnCancellationModel).where(TurnCancellationModel.turn_id.in_(turn_ids)),
                delete(TurnEventModel).where(TurnEventModel.turn_id.in_(turn_ids)),
                delete(TurnModel).where(TurnModel.conversation_id == session_id),
            ):
                await session.execute(statement)
            await session.execute(
                update(ConversationModel)
                .where(
                    ConversationModel.id == session_id,
                    ConversationModel.owner_user_id == principal.user_id,
                )
                .values(status="deleted", updated_at=func.utc_timestamp(6))
            )
        return context


class LocalSessionService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def create(
        self,
        principal: AuthenticatedPrincipal,
        *,
        workspace_id: str = "default",
        channel: str = "web",
    ) -> SessionContext:
        authorization_service.require(
            principal, Permission.AGENT_SESSION_CREATE, workspace_id=workspace_id
        )
        principal.require_workspace(workspace_id)
        context = SessionContext.create(
            user_id=principal.user_id,
            workspace_id=workspace_id,
            channel=channel,
        )
        async with self._lock:
            index = await asyncio.to_thread(_load_sessions_index)
            index.setdefault("sessions", {})[context.session_id] = {
                "created_at": time.time(),
                "last_active": time.time(),
                "user_id": context.user_id,
                "workspace_id": context.workspace_id,
                "channel": context.channel,
            }
            await asyncio.to_thread(_save_sessions_index, index)
        return context

    async def resolve(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        SessionContext(session_id=session_id)
        async with self._lock:
            index = await asyncio.to_thread(_load_sessions_index)
            metadata = index.get("sessions", {}).get(session_id)
        if metadata is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        context = SessionContext(
            session_id=session_id,
            user_id=str(metadata.get("user_id", "local")),
            workspace_id=str(metadata.get("workspace_id", "default")),
            channel=str(metadata.get("channel", "local")),
        )
        authorization_service.require(
            principal, Permission.AGENT_SESSION_READ, workspace_id=context.workspace_id
        )
        principal.require_context(context)
        return context

    async def list(self, principal: AuthenticatedPrincipal) -> list[dict[str, Any]]:
        return (await self.list_page(principal, limit=200))["items"]

    async def list_page(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        async with self._lock:
            sessions = (await asyncio.to_thread(_load_sessions_index)).get("sessions", {})
        output = []
        for session_id, metadata in sessions.items():
            context = SessionContext(
                session_id=session_id,
                user_id=str(metadata.get("user_id", "local")),
                workspace_id=str(metadata.get("workspace_id", "default")),
                channel=str(metadata.get("channel", "local")),
            )
            if not principal.can_access(context):
                continue
            output.append({"session_id": session_id, **metadata})
        output = sorted(output, key=lambda item: item.get("last_active", 0), reverse=True)
        page_limit = max(1, min(limit, 200))
        page_offset = max(0, offset)
        items = output[page_offset : page_offset + page_limit]
        return {
            "items": items,
            "total": len(output),
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(items) < len(output),
        }

    async def stats(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        page = await self.list_page(principal, limit=200)
        active_values = [
            item.get("last_active")
            for item in page["items"]
            if item.get("last_active") is not None
        ]
        last_active = max(active_values, default=None)
        return {
            "sessions_total": page["total"],
            "sessions_active": page["total"],
            "turns_total": None,
            "turns_last_24h": None,
            "last_activity_at": last_active,
        }

    async def messages(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> list[dict[str, Any]]:
        await self.resolve(principal, session_id)
        path = Path(get_session_transcript_path(session_id))
        if not path.exists():
            return []

        def read() -> list[dict[str, Any]]:
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return rows

        return await asyncio.to_thread(read)

    async def touch(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        context = await self.resolve(principal, session_id)
        authorization_service.require(
            principal, Permission.AGENT_SESSION_UPDATE, workspace_id=context.workspace_id
        )
        async with self._lock:
            index = await asyncio.to_thread(_load_sessions_index)
            metadata = index.get("sessions", {}).get(session_id)
            if metadata is not None:
                metadata["last_active"] = time.time()
                await asyncio.to_thread(_save_sessions_index, index)
        return context

    async def delete(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        context = await self.resolve(principal, session_id)
        authorization_service.require(
            principal, Permission.AGENT_SESSION_DELETE, workspace_id=context.workspace_id
        )
        async with self._lock:
            index = await asyncio.to_thread(_load_sessions_index)
            current = index.get("sessions", {}).get(session_id)
            if current is None:
                raise FileNotFoundError(session_id)
            stored = SessionContext(
                session_id=session_id,
                user_id=str(current.get("user_id", "local")),
                workspace_id=str(current.get("workspace_id", "default")),
                channel=str(current.get("channel", "local")),
            )
            if not principal.can_access(stored):
                raise AccessDeniedError(session_id)
            index["sessions"].pop(session_id, None)
            if index.get("active_session") == session_id:
                index["active_session"] = None
            await asyncio.to_thread(_save_sessions_index, index)

        def remove_local_data() -> None:
            from server.tools.vision.input_resolver import delete_session_uploads

            transcript = Path(get_session_transcript_path(session_id))
            transcript.unlink(missing_ok=True)
            shutil.rmtree(Path(CHAT_HISTORY_DIR) / session_id, ignore_errors=True)
            shutil.rmtree(Path(DATA_DIR) / session_id, ignore_errors=True)
            local_context_repository.delete_session(context)
            delete_session_uploads(context)

        await asyncio.to_thread(remove_local_data)
        from server.memory.runtime import global_memory_runtime

        await asyncio.to_thread(
            global_memory_runtime.manager(context).delete_session_archives,
            session_id,
        )
        return context


local_session_service = LocalSessionService()
