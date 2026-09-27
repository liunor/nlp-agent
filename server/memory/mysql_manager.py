"""MySQL-backed replacement for file-based scoped memory."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.mysql import insert

from configs.settings import settings
from core.session_context import SessionContext
from server.infrastructure.mysql.models import MemoryArchiveModel, MemoryCursorModel, MemoryDocumentModel
from server.memory.types import MemoryArchiveRecord, MemoryCuratorOperation, MemoryScopeKind, is_valid_memory_type
from server.memory.manager import _truncate_to_tokens
from utils.tokens import rough_token_count_estimation
from server.storage.policy import StorageBucket
from server.storage.quota import SyncStorageQuota

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|passwd|private[_-]?key)\s*[:=]")


class MySQLMemoryManager:
    """Keeps the existing MemoryManager API while persisting only in MySQL."""

    def __init__(self, context: SessionContext | str | None = None, memory_dir: object = None) -> None:
        self.context = context if isinstance(context, SessionContext) else SessionContext(session_id="default_session")
        url = settings.NLP_AGENT_DATABASE_URL.strip()
        if not url:
            raise RuntimeError("NLP_AGENT_DATABASE_URL is required for memory storage")
        self._engine = create_engine(url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)

    @staticmethod
    def _scope_for_type(memory_type: str) -> MemoryScopeKind:
        return MemoryScopeKind.WORKSPACE if memory_type in {"project", "decision", "goal"} else MemoryScopeKind.USER

    def _query(self, scope: MemoryScopeKind | None = None):
        clauses = [MemoryDocumentModel.workspace_id == self.context.workspace_id, MemoryDocumentModel.user_id == self.context.user_id]
        if scope is not None:
            clauses.append(MemoryDocumentModel.scope == scope.value)
        return clauses

    def load_memory_index(self, scope: MemoryScopeKind | None = None) -> str:
        rows = self.scan_memory_headers(scope)
        if not rows:
            return "# Memory Index\n\nNo durable memories recorded."
        return "# Memory Index\n\n" + "\n".join(f"- `{row['filename']}` — {row['description']}" for row in rows)

    def read_memory_topic(self, filename: str, scope: MemoryScopeKind | None = None) -> str:
        scopes = [scope.value] if scope else [MemoryScopeKind.USER.value, MemoryScopeKind.WORKSPACE.value]
        with self._engine.connect() as c:
            row = c.execute(select(MemoryDocumentModel.content_json).where(*self._query(), MemoryDocumentModel.document_key == filename, MemoryDocumentModel.scope.in_(scopes))).scalar()
        if row is None:
            raise FileNotFoundError(f"memory topic not found: {filename}")
        return str(row.get("content", ""))

    def save_memory_topic(self, filename: str, content: str, memory_type: str, description: str, *, scope: MemoryScopeKind | None = None) -> str:
        if not is_valid_memory_type(memory_type):
            raise ValueError(f"invalid memory type: {memory_type}")
        if _SECRET_RE.search(content):
            raise ValueError("memory content appears to contain a secret")
        filename = filename if filename.endswith(".md") else f"{filename}.md"
        chosen = scope or self._scope_for_type(memory_type)
        key = f"{self.context.workspace_id}:{self.context.user_id}:{chosen.value}:{filename}"
        payload = {"filename": filename, "name": filename[:-3], "description": description.strip(), "type": memory_type, "content": content.strip()}
        with self._engine.begin() as c:
            quota = SyncStorageQuota(c, owner_user_id=self.context.user_id)
            old = c.execute(
                select(MemoryDocumentModel.content_json).where(
                    *self._query(chosen), MemoryDocumentModel.document_key == filename
                )
            ).scalar()
            old_size = len(str(old).encode("utf-8")) if old is not None else 0
            new_size = len(str(payload).encode("utf-8"))
            reservation = quota.reserve(
                StorageBucket.CORE,
                max(0, new_size - old_size) + 256,
                resource_type="memory_document",
                resource_key=key,
            )
            c.execute(insert(MemoryDocumentModel).values(id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)), user_id=self.context.user_id, workspace_id=self.context.workspace_id, scope=chosen.value, document_key=filename, content_json=payload).on_duplicate_key_update(content_json=payload, revision=MemoryDocumentModel.revision + 1))
            quota.finalize(reservation, reconcile=True)
        return filename

    def delete_memory_topic(self, filename: str, *, scope: MemoryScopeKind | None = None) -> None:
        with self._engine.begin() as c:
            result = c.execute(delete(MemoryDocumentModel).where(*self._query(scope), MemoryDocumentModel.document_key == filename))
        if not result.rowcount:
            raise FileNotFoundError(f"memory topic not found: {filename}")
        with self._engine.begin() as c:
            SyncStorageQuota(c, owner_user_id=self.context.user_id).reconcile()

    def scan_memory_headers(self, scope: MemoryScopeKind | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as c:
            rows = c.execute(select(MemoryDocumentModel.__table__).where(*self._query(scope)).order_by(MemoryDocumentModel.updated_at.desc())).mappings().all()
        return [{"filename": item["document_key"], "name": item["content_json"].get("name", item["document_key"]), "type": item["content_json"].get("type", "unknown"), "description": item["content_json"].get("description", ""), "preview": str(item["content_json"].get("content", ""))[:200], "mtime": item["updated_at"].timestamp() if item["updated_at"] else 0, "scope": item["scope"]} for item in rows]

    def append_archive(self, *, source_id: str, summary: str, source_message_ids: Iterable[str] = ()) -> MemoryArchiveRecord:
        if not summary.strip():
            raise ValueError("archive summary cannot be empty")
        with self._engine.begin() as c:
            existing = c.execute(select(MemoryArchiveModel.__table__).where(MemoryArchiveModel.user_id == self.context.user_id, MemoryArchiveModel.workspace_id == self.context.workspace_id, MemoryArchiveModel.source_id == source_id)).mappings().first()
            if existing:
                return self._archive_mapping(existing)
            cursor = int(c.execute(select(func.coalesce(func.max(MemoryArchiveModel.cursor), 0)).where(MemoryArchiveModel.user_id == self.context.user_id, MemoryArchiveModel.workspace_id == self.context.workspace_id)).scalar_one()) + 1
            payload = {"summary": summary.strip(), "source_message_ids": list(source_message_ids)}
            row = {"id": str(uuid.uuid4()), "user_id": self.context.user_id, "workspace_id": self.context.workspace_id, "session_id": self.context.session_id, "source_id": source_id, "cursor": cursor, "payload_json": payload}
            quota = SyncStorageQuota(c, owner_user_id=self.context.user_id)
            reservation = quota.reserve(StorageBucket.CORE, len(str(payload).encode("utf-8")) + 256, resource_type="memory_archive", resource_key=source_id)
            c.execute(insert(MemoryArchiveModel).values(**row))
            quota.finalize(reservation, reconcile=True)
        return self._archive_mapping(row)

    def _archive_record(self, row: MemoryArchiveModel) -> MemoryArchiveRecord:
        return MemoryArchiveRecord(cursor=row.cursor, source_id=row.source_id, workspace_id=row.workspace_id, user_id=row.user_id, session_id=row.session_id, agent_id=self.context.agent_id, summary=str(row.payload_json.get("summary", "")), source_message_ids=tuple(row.payload_json.get("source_message_ids", [])))

    def _archive_mapping(self, row: Any) -> MemoryArchiveRecord:
        return MemoryArchiveRecord(cursor=row["cursor"], source_id=row["source_id"], workspace_id=row["workspace_id"], user_id=row["user_id"], session_id=row["session_id"], agent_id=self.context.agent_id, summary=str(row["payload_json"].get("summary", "")), source_message_ids=tuple(row["payload_json"].get("source_message_ids", [])))

    def read_archives(self, *, since_cursor: int = 0, session_id: str | None = None) -> list[MemoryArchiveRecord]:
        clauses = [MemoryArchiveModel.user_id == self.context.user_id, MemoryArchiveModel.workspace_id == self.context.workspace_id, MemoryArchiveModel.cursor > since_cursor]
        if session_id is not None: clauses.append(MemoryArchiveModel.session_id == session_id)
        with self._engine.connect() as c:
            rows = c.execute(select(MemoryArchiveModel.__table__).where(*clauses).order_by(MemoryArchiveModel.cursor)).mappings().all()
        return [self._archive_mapping(row) for row in rows]

    def delete_session_archives(self, session_id: str) -> int:
        with self._engine.begin() as c:
            count = int(c.execute(delete(MemoryArchiveModel).where(MemoryArchiveModel.session_id == session_id)).rowcount or 0)
            if count:
                SyncStorageQuota(c, owner_user_id=self.context.user_id).reconcile()
            return count

    def get_curator_cursor(self) -> int:
        key = f"{self.context.workspace_id}:{self.context.user_id}"
        with self._engine.connect() as c:
            return int(c.execute(select(MemoryCursorModel.cursor).where(MemoryCursorModel.scope_key == key)).scalar() or 0)

    def set_curator_cursor(self, cursor: int) -> None:
        key = f"{self.context.workspace_id}:{self.context.user_id}"
        with self._engine.begin() as c:
            c.execute(insert(MemoryCursorModel).values(scope_key=key, cursor=max(0, cursor)).on_duplicate_key_update(cursor=max(0, cursor)))

    def check_stale_memories(self, days: int = 30) -> list[dict[str, Any]]:
        return []

    def build_injection_text(self, *, max_tokens: int, max_topics: int, recent_archive_tokens: int) -> str:
        parts = [self.load_memory_index()]
        topic_budget = max(0, max_tokens - recent_archive_tokens)
        for row in self.scan_memory_headers()[:max_topics]:
            content = self.read_memory_topic(row["filename"], MemoryScopeKind(row["scope"]))
            if rough_token_count_estimation("\n\n".join([*parts, content])) > topic_budget:
                break
            parts.append(content)
        recent = self.read_archives(
            since_cursor=self.get_curator_cursor(),
            session_id=self.context.session_id,
        )
        if recent and recent_archive_tokens:
            text = "\n".join(
                f"- [{row.cursor}] {row.summary}" for row in recent[-20:]
            )
            parts.append(
                "## Current session archived context\n"
                + _truncate_to_tokens(text, recent_archive_tokens)
            )
        return _truncate_to_tokens("\n\n".join(parts), max_tokens)

    def apply_curator_operation(self, operation: MemoryCuratorOperation) -> None:
        if operation.operation == "ignore": return
        if operation.operation == "delete":
            self.delete_memory_topic(operation.filename, scope=operation.scope); return
        self.save_memory_topic(operation.filename, operation.content, operation.memory_type, operation.description, scope=operation.scope)
