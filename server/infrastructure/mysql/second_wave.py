"""Second-wave durable state ports backed by MySQL JSON columns."""
from __future__ import annotations

import uuid
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentCheckpointModel, ConversationModel, ConversationTranscriptModel, MemoryDocumentModel, RuntimeConfigVersionModel, ToolAuditModel
from server.storage.policy import StorageBucket
from server.storage.quota import AsyncStorageQuota


class SecondWaveStore:
    async def save_checkpoint(self, session: AsyncSession, *, session_id: str, checkpoint_ns: str, checkpoint_id: str, checkpoint: dict[str, Any], metadata: dict[str, Any]) -> AgentCheckpointModel:
        owner = await session.scalar(select(ConversationModel.owner_user_id).where(ConversationModel.id == session_id))
        if owner is None:
            raise PermissionError("checkpoint identity is required")
        quota = AsyncStorageQuota(session, owner_user_id=str(owner), roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(checkpoint, default=str).encode()) + len(json.dumps(metadata, default=str).encode()) + 256, resource_type="agent_checkpoint", resource_key=f"{session_id}:{checkpoint_id}")
        row = AgentCheckpointModel(id=str(uuid.uuid4()), session_id=session_id, checkpoint_ns=checkpoint_ns, checkpoint_id=checkpoint_id, checkpoint_json=checkpoint, metadata_json=metadata)
        session.add(row); await session.flush(); await quota.finalize(reservation, reconcile=True); return row

    async def append_transcript(self, session: AsyncSession, *, session_id: str, message_uuid: str, parent_uuid: str | None, message_type: str, role: str, content: Any, tool: dict[str, Any] | None = None, usage: dict[str, Any] | None = None) -> ConversationTranscriptModel:
        conversation_owner = await session.scalar(select(ConversationModel.owner_user_id).where(ConversationModel.id == session_id))
        if conversation_owner is None:
            raise PermissionError("transcript identity is required")
        quota = AsyncStorageQuota(session, owner_user_id=str(conversation_owner), roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(content, default=str).encode()) + len(json.dumps(tool or {}, default=str).encode()) + len(json.dumps(usage or {}, default=str).encode()) + 256, resource_type="conversation_transcript", resource_key=f"{session_id}:{message_uuid}")
        row = ConversationTranscriptModel(id=str(uuid.uuid4()), session_id=session_id, message_uuid=message_uuid, parent_uuid=parent_uuid, message_type=message_type, role=role, content_json=content, tool_json=tool, usage_json=usage)
        session.add(row); await session.flush(); await quota.finalize(reservation, reconcile=True); return row

    async def save_memory(self, session: AsyncSession, *, user_id: str, workspace_id: str, scope: str, document_key: str, content: Any) -> MemoryDocumentModel:
        quota = AsyncStorageQuota(session, owner_user_id=user_id, roles=None)
        reservation = await quota.reserve(StorageBucket.CORE, len(json.dumps(content, default=str).encode()) + 256, resource_type="memory_document", resource_key=f"{workspace_id}:{document_key}")
        row = MemoryDocumentModel(id=str(uuid.uuid4()), user_id=user_id, workspace_id=workspace_id, scope=scope, document_key=document_key, content_json=content)
        session.add(row); await session.flush(); await quota.finalize(reservation, reconcile=True); return row

    async def audit_tool(self, session: AsyncSession, *, tool_name: str, status: str, request: dict[str, Any], turn_id: str | None = None, operation_id: str | None = None, actor_id: str | None = None, result: dict[str, Any] | None = None) -> ToolAuditModel:
        row = ToolAuditModel(id=str(uuid.uuid4()), turn_id=turn_id, operation_id=operation_id, tool_name=tool_name, actor_id=actor_id, status=status, request_json=request, result_json=result)
        session.add(row); await session.flush(); return row

    async def save_runtime_config(self, session: AsyncSession, *, scope: str, revision: int, config: dict[str, Any]) -> RuntimeConfigVersionModel:
        row = RuntimeConfigVersionModel(id=str(uuid.uuid4()), scope=scope, revision=revision, config_json=config)
        session.add(row); await session.flush(); return row

    async def latest_checkpoint(self, session: AsyncSession, session_id: str) -> AgentCheckpointModel | None:
        return await session.scalar(select(AgentCheckpointModel).where(AgentCheckpointModel.session_id == session_id).order_by(AgentCheckpointModel.created_at.desc()).limit(1))
