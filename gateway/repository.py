"""SQLite WAL persistence for Gateway turns, replayable events, and outbox."""

from __future__ import annotations

import json
import random
import hashlib
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from gateway.contracts import (
    GatewayEvent,
    GatewayEventType,
    KnowledgeBookRevisionConflictError,
    TeachingConfigurationError,
    GuidedSessionRef,
    TurnRecord,
    TurnStatus,
)
from core.learning import ExerciseState, LearningContext, LearningProgress, knowledge_point_ids


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GatewayRepository:
    def __init__(self, path: str | Path, *, knowledge_point_prompt_budget: int = 12_000) -> None:
        self.path = Path(path)
        self.knowledge_point_prompt_budget = max(1, knowledge_point_prompt_budget)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=5000;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS gateway_turns (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    learning_context_json TEXT,
                    learning_progress_json TEXT,
                    guided_session_id TEXT,
                    guided_blueprint_id TEXT,
                    guided_blueprint_snapshot_sha256 TEXT,
                    guided_session_attempts INTEGER,
                    guided_session_status TEXT,
                    exercise_state_json TEXT,
                    final_text TEXT,
                    error_kind TEXT,
                    error_message TEXT,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(user_id, session_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_turns_session
                    ON gateway_turns(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gateway_turns_status
                    ON gateway_turns(status, created_at);
                CREATE TABLE IF NOT EXISTS gateway_events (
                    event_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(turn_id, sequence),
                    FOREIGN KEY(turn_id) REFERENCES gateway_turns(turn_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_events_session
                    ON gateway_events(session_id, created_at);
                CREATE TABLE IF NOT EXISTS gateway_user_settings (
                    user_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                -- Teaching assets are deliberately independent from chat sessions,
                -- turns, and user UI settings.  Editing a course cannot mutate a
                -- learner transcript or LangGraph checkpoint.
                CREATE TABLE IF NOT EXISTS gateway_teaching_catalogs (
                    workspace_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    catalog_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_knowledge_pages (
                    workspace_id TEXT NOT NULL,
                    knowledge_point_id TEXT NOT NULL,
                    draft_markdown TEXT NOT NULL DEFAULT '',
                    published_markdown TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    published_revision INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, knowledge_point_id)
                );
                CREATE TABLE IF NOT EXISTS gateway_knowledge_book_assets (
                    workspace_id TEXT NOT NULL,
                    asset_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    draft_content BLOB NOT NULL,
                    published_content BLOB,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, asset_path)
                );
                CREATE TABLE IF NOT EXISTS gateway_blueprints (
                    workspace_id TEXT NOT NULL, blueprint_id TEXT NOT NULL, kind TEXT NOT NULL,
                    topic_id TEXT NOT NULL, knowledge_point_id TEXT NOT NULL, level TEXT,
                    status TEXT NOT NULL, blueprint_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, blueprint_id)
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_blueprints_assignment
                    ON gateway_blueprints(workspace_id, kind, topic_id, knowledge_point_id, level, status);
                CREATE TABLE IF NOT EXISTS gateway_exercise_sessions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    blueprint_snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_exercise_sessions_chat
                    ON gateway_exercise_sessions(session_id, topic_id, mode, status);
                CREATE TABLE IF NOT EXISTS gateway_exercise_questions (
                    id TEXT PRIMARY KEY,
                    exercise_session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    rubric_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(exercise_session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS gateway_exercise_attempts (
                    id TEXT PRIMARY KEY,
                    exercise_question_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    rubric_matches_json TEXT NOT NULL,
                    normalized_score INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    feedback TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_learning_evidence (
                    id TEXT PRIMARY KEY,
                    exercise_session_id TEXT NOT NULL,
                    exercise_question_id TEXT NOT NULL,
                    blueprint_snapshot_json TEXT NOT NULL,
                    question TEXT NOT NULL,
                    learner_answer TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    rubric_matches_json TEXT NOT NULL,
                    normalized_score INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    knowledge_point_ids_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    UNIQUE(exercise_question_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS gateway_guided_sessions (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, topic_id TEXT NOT NULL, status TEXT NOT NULL,
                    objective TEXT NOT NULL, stage TEXT NOT NULL, learner_responses_json TEXT NOT NULL,
                    known_concepts_json TEXT NOT NULL, misconceptions_json TEXT NOT NULL,
                    last_question TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    guided_blueprint_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_guided_sessions_active
                    ON gateway_guided_sessions(session_id, topic_id) WHERE status='active';
                DROP TABLE IF EXISTS gateway_outbox;
                """
            )
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(gateway_turns)")}
            for name in (
                "learning_context_json", "learning_progress_json", "exercise_state_json",
                "guided_session_id", "guided_blueprint_id", "guided_blueprint_snapshot_sha256",
                "guided_session_attempts", "guided_session_status",
            ):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE gateway_turns ADD COLUMN {name} TEXT")
            exercise_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(gateway_exercise_sessions)")}
            if "completed_at" not in exercise_columns:
                self._conn.execute("ALTER TABLE gateway_exercise_sessions ADD COLUMN completed_at TEXT")
            guided_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(gateway_guided_sessions)")}
            if "guided_blueprint_json" not in guided_columns:
                self._conn.execute("ALTER TABLE gateway_guided_sessions ADD COLUMN guided_blueprint_json TEXT NOT NULL DEFAULT '{}'")

    def create_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        workspace_id: str,
        user_id: str,
        input_text: str,
        idempotency_key: str | None,
        learning_context: LearningContext | None = None,
        learning_progress: LearningProgress | None = None,
        guided_session_id: str | None = None,
        guided_blueprint_id: str | None = None,
        guided_blueprint_snapshot_sha256: str | None = None,
        guided_session_attempts: int | None = None,
        guided_session_status: str | None = None,
        exercise_state: ExerciseState | None = None,
        dispatch_payload: str | None = None,
    ) -> tuple[TurnRecord, bool]:
        with self._lock, self._conn:
            if idempotency_key:
                existing = self._conn.execute(
                    "SELECT * FROM gateway_turns WHERE user_id=? AND session_id=? "
                    "AND idempotency_key=?",
                    (user_id, session_id, idempotency_key),
                ).fetchone()
                if existing:
                    return self._turn(existing), True
            created_at = _now()
            self._conn.execute(
                """INSERT INTO gateway_turns (
                   turn_id,session_id,workspace_id,user_id,status,input_text,
                   learning_context_json,learning_progress_json,guided_session_id,guided_blueprint_id,
                   guided_blueprint_snapshot_sha256,guided_session_attempts,guided_session_status,
                   exercise_state_json,idempotency_key,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    turn_id,
                    session_id,
                    workspace_id,
                    user_id,
                    TurnStatus.ACCEPTED.value,
                    input_text,
                    learning_context.model_dump_json() if learning_context else None,
                    learning_progress.model_dump_json() if learning_progress else None,
                    guided_session_id,
                    guided_blueprint_id,
                    guided_blueprint_snapshot_sha256,
                    guided_session_attempts,
                    guided_session_status,
                    exercise_state.model_dump_json() if exercise_state else None,
                    idempotency_key,
                    created_at,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
            return self._turn(row), False

    def update_turn(
        self,
        turn_id: str,
        status: TurnStatus,
        *,
        final_text: str | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
        exercise_state: ExerciseState | None = None,
        dispatch_payload: str | None = None,
    ) -> TurnRecord:
        fields: dict[str, Any] = {
            "status": status.value,
            "final_text": final_text,
            "error_kind": error_kind,
            "error_message": error_message[:1000] if error_message else None,
        }
        if status == TurnStatus.ACCEPTED:
            fields["started_at"] = None
            fields["completed_at"] = None
        if status == TurnStatus.RUNNING:
            fields["started_at"] = _now()
        if status in {
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
            TurnStatus.INTERRUPTED,
        }:
            fields["completed_at"] = _now()
        if exercise_state is not None:
            fields["exercise_state_json"] = exercise_state.model_dump_json()
        assignments = ",".join(f"{key}=?" for key in fields)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE gateway_turns SET {assignments} WHERE turn_id=?",
                (*fields.values(), turn_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(turn_id)
            row = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
            return self._turn(row)

    def update_turn_guided_status(self, turn_id: str, *, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE gateway_turns SET guided_session_status=? WHERE turn_id=?",
                (status, turn_id),
            )

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
        return self._turn(row) if row else None

    def active_turn_for_session(
        self, session_id: str, *, exclude_turn_id: str | None = None
    ) -> TurnRecord | None:
        exclusion = " AND turn_id<>?" if exclude_turn_id else ""
        args: tuple[Any, ...] = (
            session_id,
            TurnStatus.ACCEPTED.value,
            TurnStatus.RUNNING.value,
            *((exclude_turn_id,) if exclude_turn_id else ()),
        )
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE session_id=? AND status IN (?,?) "
                + exclusion + " ORDER BY created_at DESC LIMIT 1",
                args,
            ).fetchone()
        return self._turn(row) if row else None

    def append_event(
        self,
        *,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict[str, Any] | None = None,
    ) -> GatewayEvent:
        with self._lock, self._conn:
            sequence = int(
                self._conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM gateway_events WHERE turn_id=?",
                    (turn_id,),
                ).fetchone()[0]
            )
            event = GatewayEvent(
                event_id=uuid.uuid4().hex,
                turn_id=turn_id,
                session_id=session_id,
                sequence=sequence,
                type=event_type,
                payload=payload or {},
            )
            self._conn.execute(
                "INSERT INTO gateway_events VALUES (?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.turn_id,
                    event.session_id,
                    event.sequence,
                    event.type.value,
                    event.created_at.isoformat(),
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                ),
            )
            return event

    def events_after(
        self, turn_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[GatewayEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM gateway_events WHERE turn_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (turn_id, max(0, after_sequence), min(max(1, limit), 2000)),
            ).fetchall()
        return [self._event(row) for row in rows]

    def ensure_event(
        self,
        *,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict[str, Any] | None = None,
    ) -> GatewayEvent:
        """Return an existing event of this type or append it exactly once."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_events WHERE turn_id=? AND event_type=? "
                "ORDER BY sequence LIMIT 1",
                (turn_id, event_type.value),
            ).fetchone()
            if row is not None:
                return self._event(row)
            return self.append_event(
                turn_id=turn_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )

    def list_turns(self, session_id: str, *, limit: int = 100) -> list[TurnRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE session_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, min(max(1, limit), 500)),
            ).fetchall()
        return [self._turn(row) for row in rows]

    def latest_learning_state(
        self, session_id: str
    ) -> tuple[LearningContext | None, LearningProgress | None, ExerciseState | None]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM gateway_turns
                   WHERE session_id=? AND NOT (status=? AND error_kind='turn_conflict')
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, TurnStatus.FAILED.value),
            ).fetchone()
        if row is None:
            return None, None, None
        turn = self._turn(row)
        return turn.learning_context, turn.learning_progress, turn.exercise_state

    def turn_for_idempotency(
        self, *, user_id: str, session_id: str, idempotency_key: str
    ) -> TurnRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_turns WHERE user_id=? AND session_id=? AND idempotency_key=?",
                (user_id, session_id, idempotency_key),
            ).fetchone()
        return self._turn(row) if row else None

    def teaching_topic(self, workspace_id: str, topic_id: str) -> dict[str, Any] | None:
        catalog = self.get_teaching_catalog(workspace_id)["catalog"]
        topic = next(
            (
                item for item in catalog.get("topics", [])
                if item.get("id") == topic_id and item.get("status", "enabled") == "enabled"
            ),
            None,
        )
        if topic is None:
            return None
        enabled_points = sorted(
            (
                point for point in topic.get("knowledge_points", [])
                if point.get("status", "enabled") == "enabled"
            ),
            key=lambda point: int(point.get("sort_order", 0)),
        )
        knowledge_points = [
            str(point.get("markdown") or point.get("name") or "")
            for point in enabled_points
        ]
        total_characters = sum(len(point) for point in knowledge_points)
        if total_characters > self.knowledge_point_prompt_budget:
            raise TeachingConfigurationError(
                f"主题“{topic.get('name', topic_id)}”的知识点内容超过提示词预算"
            )
        return {
            "id": topic.get("id"),
            "name": topic.get("name", ""),
            "description": topic.get("description", ""),
            "knowledge_points": knowledge_points,
        }

    def active_exercise_session(
        self, *, session_id: str, topic_id: str, mode: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM gateway_exercise_sessions
                   WHERE session_id=? AND topic_id=? AND mode=? AND status='active'
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, topic_id, mode),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["blueprint_snapshot"] = json.loads(value.pop("blueprint_snapshot_json"))
        return value

    def _guided_session(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for field in ("learner_responses", "known_concepts", "misconceptions"):
            value[field] = json.loads(value.pop(f"{field}_json"))
        value["guided_blueprint"] = json.loads(value.pop("guided_blueprint_json") or "{}")
        return value

    def expire_guided_sessions(self, *, session_id: str, idle_minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).isoformat()
        now = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE gateway_guided_sessions SET status='expired', completed_at=?, updated_at=?
                   WHERE session_id=? AND status='active' AND updated_at<?""",
                (now, now, session_id, cutoff),
            )
        return cursor.rowcount

    def active_guided_session(self, *, session_id: str, topic_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM gateway_guided_sessions WHERE session_id=? AND topic_id=?
                   AND status='active' ORDER BY created_at DESC LIMIT 1""",
                (session_id, topic_id),
            ).fetchone()
        return self._guided_session(row) if row is not None else None

    def start_or_resume_guided_session(
        self, *, session_id: str, workspace_id: str, user_id: str, topic_id: str, first_message: str,
        guided_blueprint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = self.active_guided_session(session_id=session_id, topic_id=topic_id)
        if active is not None:
            responses = [*active["learner_responses"], first_message][:20]
            with self._lock, self._conn:
                self._conn.execute(
                    """UPDATE gateway_guided_sessions SET learner_responses_json=?, attempts=?,
                       stage='reasoning', updated_at=? WHERE id=?""",
                    (json.dumps(responses, ensure_ascii=False), int(active["attempts"]) + 1, _now(), active["id"]),
                )
            return self.active_guided_session(session_id=session_id, topic_id=topic_id) or active
        now = _now()
        record = {
            "id": str(uuid.uuid4()), "session_id": session_id, "workspace_id": workspace_id,
            "user_id": user_id, "topic_id": topic_id, "status": "active", "objective": first_message,
            "stage": "opening", "learner_responses": [], "known_concepts": [], "misconceptions": [],
            "last_question": "", "attempts": 0, "created_at": now, "updated_at": now, "completed_at": None,
            "guided_blueprint": guided_blueprint or {},
        }
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO gateway_guided_sessions(
                   id,session_id,workspace_id,user_id,topic_id,status,objective,stage,
                   learner_responses_json,known_concepts_json,misconceptions_json,last_question,
                   attempts,guided_blueprint_json,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record["id"], session_id, workspace_id, user_id, topic_id, "active", first_message,
                 "opening", "[]", "[]", "[]", "", 0, json.dumps(record["guided_blueprint"], ensure_ascii=False), now, now, None),
            )
        return record

    def advance_guided_session(
        self, guided_session_id: str, *, tutor_message: str, known_concepts: object = None,
        misconceptions: object = None, completed: bool = False,
    ) -> None:
        known = [str(item)[:300] for item in known_concepts if str(item).strip()][:30] if isinstance(known_concepts, list) else []
        mistakes = [str(item)[:300] for item in misconceptions if str(item).strip()][:20] if isinstance(misconceptions, list) else []
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE gateway_guided_sessions SET stage=?, status=?, known_concepts_json=?,
                   misconceptions_json=?, last_question=?, updated_at=?, completed_at=?
                   WHERE id=? AND status='active'""",
                (
                    "completed" if completed else "awaiting_learner_response",
                    "completed" if completed else "active", json.dumps(known, ensure_ascii=False),
                    json.dumps(mistakes, ensure_ascii=False), tutor_message[:2_000], now,
                    now if completed else None, guided_session_id,
                ),
            )

    def end_guided_sessions(self, *, session_id: str, status: str = "cancelled") -> int:
        if status not in {"completed", "cancelled", "expired"}:
            raise ValueError("invalid guided session terminal status")
        now = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE gateway_guided_sessions SET status=?, completed_at=?, updated_at=?
                   WHERE session_id=? AND status='active'""",
                (status, now, now, session_id),
            )
        return cursor.rowcount

    def list_question_turns(
        self,
        *,
        workspace_id: str,
        since: str,
    ) -> list[dict[str, Any]]:
        """Teacher read model: structured question rows (no question text).

        The time window is the only bound; aggregation must reflect every turn.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT session_id,user_id,error_kind,created_at,learning_context_json
                   FROM gateway_turns WHERE workspace_id=? AND created_at>=?
                   ORDER BY created_at""",
                (workspace_id, since),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            context = json.loads(row["learning_context_json"]) if row["learning_context_json"] else {}
            result.append(
                {
                    "session_id": row["session_id"],
                    "user_id": row["user_id"],
                    "has_error": bool(row["error_kind"]),
                    "topic_id": context.get("topic_id"),
                    "level": context.get("level"),
                    "mode": context.get("mode"),
                    "day": str(row["created_at"])[:10],
                }
            )
        return result

    def exercise_evidence_stats(
        self,
        *,
        workspace_id: str,
        since: str,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.normalized_score,e.passed,e.knowledge_point_ids_json,e.blueprint_snapshot_json,s.topic_id,s.mode
                   FROM gateway_learning_evidence e
                   JOIN gateway_exercise_sessions s ON s.id=e.exercise_session_id
                   WHERE s.workspace_id=? AND s.completed_at>=?
                   ORDER BY s.completed_at DESC LIMIT ?""",
                (workspace_id, since, min(max(1, limit), 10_000)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            kp_ids = json.loads(row["knowledge_point_ids_json"] or "[]") if row["knowledge_point_ids_json"] else []
            if not kp_ids:
                blueprint = json.loads(row["blueprint_snapshot_json"] or "{}") if row["blueprint_snapshot_json"] else {}
                kp_ids = knowledge_point_ids(blueprint)
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "mode": row["mode"],
                    "knowledge_point_ids": [str(item) for item in kp_ids if item],
                    "score": int(row["normalized_score"]),
                    "passed": bool(row["passed"]),
                }
            )
        return result

    def exercise_criterion_stats(
        self,
        *,
        workspace_id: str,
        since: str,
        limit: int = 20_000,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.rubric_matches_json,s.topic_id,s.blueprint_snapshot_json
                   FROM gateway_exercise_attempts a
                   JOIN gateway_exercise_questions q ON q.id=a.exercise_question_id
                   JOIN gateway_exercise_sessions s ON s.id=q.exercise_session_id
                   WHERE s.workspace_id=? AND s.completed_at>=?
                   ORDER BY s.completed_at DESC LIMIT ?""",
                (workspace_id, since, min(max(1, limit), 50_000)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            blueprint = json.loads(row["blueprint_snapshot_json"] or "{}") if row["blueprint_snapshot_json"] else {}
            matches = json.loads(row["rubric_matches_json"] or "[]") if row["rubric_matches_json"] else []
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "knowledge_point_ids": knowledge_point_ids(blueprint),
                    "matches": [m for m in matches if isinstance(m, dict)],
                }
            )
        return result

    def guided_session_stats(
        self,
        *,
        workspace_id: str,
        since: str,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT topic_id,misconceptions_json FROM gateway_guided_sessions
                   WHERE workspace_id=? AND (completed_at>=? OR completed_at IS NULL)
                   ORDER BY created_at DESC LIMIT ?""",
                (workspace_id, since, min(max(1, limit), 10_000)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            misconceptions = json.loads(row["misconceptions_json"] or "[]") if row["misconceptions_json"] else []
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "misconception_count": len(misconceptions) if isinstance(misconceptions, list) else 0,
                }
            )
        return result

    def latest_event_sequence(self, turn_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM gateway_events WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
        return int(row[0])

    def get_user_settings(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision,settings_json,updated_at FROM gateway_user_settings "
                "WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if row is None:
            return {"revision": 0, "settings": {}, "updated_at": None}
        return {
            "revision": int(row["revision"]),
            "settings": json.loads(row["settings_json"] or "{}"),
            "updated_at": row["updated_at"],
        }

    def update_user_settings(
        self,
        user_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock, self._conn:
            current = self.get_user_settings(user_id)
            merged = {**current["settings"], **changes}
            revision = int(current["revision"]) + 1
            updated_at = _now()
            self._conn.execute(
                """INSERT INTO gateway_user_settings(user_id,revision,settings_json,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     revision=excluded.revision,
                     settings_json=excluded.settings_json,
                     updated_at=excluded.updated_at""",
                (
                    user_id,
                    revision,
                    json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                    updated_at,
                ),
            )
        return {"revision": revision, "settings": merged, "updated_at": updated_at}

    def get_teaching_catalog(self, workspace_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision,catalog_json,updated_at FROM gateway_teaching_catalogs WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return {"revision": 0, "catalog": {"workspace_id": workspace_id, "topics": [], "exercise_blueprints": [], "review_blueprints": [], "guided_blueprints": []}, "updated_at": None}
        return {"revision": int(row["revision"]), "catalog": json.loads(row["catalog_json"]), "updated_at": row["updated_at"]}

    def update_teaching_catalog(self, workspace_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._conn:
            current = self.get_teaching_catalog(workspace_id)
            revision = int(current["revision"]) + 1
            updated_at = _now()
            self._conn.execute(
                """INSERT INTO gateway_teaching_catalogs(workspace_id,revision,catalog_json,updated_at)
                   VALUES (?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
                   revision=excluded.revision,catalog_json=excluded.catalog_json,updated_at=excluded.updated_at""",
                (workspace_id, revision, json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), updated_at),
            )
            self._conn.execute("DELETE FROM gateway_blueprints WHERE workspace_id=?", (workspace_id,))
            for kind, field in (("exercise", "exercise_blueprints"), ("review", "review_blueprints"), ("guided", "guided_blueprints")):
                for blueprint in catalog.get(field, []):
                    self._conn.execute(
                        """INSERT INTO gateway_blueprints(workspace_id,blueprint_id,kind,topic_id,knowledge_point_id,level,status,blueprint_json,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (workspace_id, str(blueprint["id"]), kind, str(blueprint["topic_id"]),
                         str(blueprint.get("knowledge_point_id", "legacy_unassigned")), None,
                         str(blueprint.get("status", "draft")),
                         json.dumps(blueprint, ensure_ascii=False, separators=(",", ":")), updated_at),
                    )
        return {"revision": revision, "catalog": catalog, "updated_at": updated_at}

    def get_knowledge_page(self, workspace_id: str, knowledge_point_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,
                          revision,published_revision,updated_at
                   FROM gateway_knowledge_pages
                   WHERE workspace_id=? AND knowledge_point_id=?""",
                (workspace_id, knowledge_point_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_knowledge_pages(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,
                          revision,published_revision,updated_at
                   FROM gateway_knowledge_pages
                   WHERE workspace_id=?""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_published_knowledge_page(self, workspace_id: str, knowledge_point_id: str) -> dict[str, Any] | None:
        row = self.get_knowledge_page(workspace_id, knowledge_point_id)
        if row is None or row["published_markdown"] is None:
            return None
        return row

    @staticmethod
    def _check_knowledge_page_revision(current: dict[str, Any] | None, expected_revision: int | None) -> int:
        revision = int(current["revision"]) if current is not None else 0
        if expected_revision is not None and expected_revision != revision:
            raise KnowledgeBookRevisionConflictError(f"知识点教材版本冲突：当前版本为 {revision}")
        return revision

    def update_knowledge_page(
        self,
        workspace_id: str,
        knowledge_point_id: str,
        draft_markdown: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._conn:
            current = self.get_knowledge_page(workspace_id, knowledge_point_id)
            revision = self._check_knowledge_page_revision(current, expected_revision) + 1
            updated_at = _now()
            if current is None:
                self._conn.execute(
                    """INSERT INTO gateway_knowledge_pages(
                               workspace_id,knowledge_point_id,draft_markdown,revision,updated_at)
                       VALUES (?,?,?,?,?)""",
                    (workspace_id, knowledge_point_id, draft_markdown, revision, updated_at),
                )
            else:
                self._conn.execute(
                    """UPDATE gateway_knowledge_pages
                       SET draft_markdown=?,revision=?,updated_at=?
                       WHERE workspace_id=? AND knowledge_point_id=?""",
                    (draft_markdown, revision, updated_at, workspace_id, knowledge_point_id),
                )
        return self.get_knowledge_page(workspace_id, knowledge_point_id)  # type: ignore[return-value]

    def publish_knowledge_page(
        self,
        workspace_id: str,
        knowledge_point_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock, self._conn:
            current = self.get_knowledge_page(workspace_id, knowledge_point_id)
            if current is None:
                raise ValueError("教材页面尚未保存草稿")
            revision = self._check_knowledge_page_revision(current, expected_revision)
            if not str(current["draft_markdown"]).strip():
                raise ValueError("教材正文为空，不能发布")
            updated_at = _now()
            self._conn.execute(
                """UPDATE gateway_knowledge_pages
                   SET published_markdown=draft_markdown,
                       published_revision=?,updated_at=?
                   WHERE workspace_id=? AND knowledge_point_id=?""",
                (revision, updated_at, workspace_id, knowledge_point_id),
            )
            for asset_path in self._asset_paths_from_markdown(str(current["draft_markdown"])):
                self._conn.execute(
                    """UPDATE gateway_knowledge_book_assets
                       SET published_content=draft_content,updated_at=?
                       WHERE workspace_id=? AND asset_path=?""",
                    (_now(), workspace_id, asset_path),
                )
        return self.get_knowledge_page(workspace_id, knowledge_point_id)  # type: ignore[return-value]

    @staticmethod
    def _asset_paths_from_markdown(markdown: str) -> set[str]:
        paths: set[str] = set()
        for raw_path in re.findall(r"/assets/(assets/[^)\s\"']+)", markdown):
            path = unquote(raw_path)
            if path.startswith("assets/") and ".." not in path.split("/"):
                paths.add(path)
        return paths

    def apply_knowledge_book_import(
        self,
        workspace_id: str,
        pages: list[dict[str, Any]],
        assets: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply a validated package atomically after checking every revision."""
        with self._lock, self._conn:
            currents: dict[str, dict[str, Any] | None] = {}
            for page in pages:
                point_id = str(page["knowledge_point_id"])
                current = self.get_knowledge_page(workspace_id, point_id)
                self._check_knowledge_page_revision(current, int(page["expected_revision"]))
                currents[point_id] = current

            for page in pages:
                point_id = str(page["knowledge_point_id"])
                current = currents[point_id]
                revision = int(page["expected_revision"]) + 1
                updated_at = _now()
                if current is None:
                    self._conn.execute(
                        """INSERT INTO gateway_knowledge_pages(
                           workspace_id,knowledge_point_id,draft_markdown,revision,updated_at)
                           VALUES (?,?,?,?,?)""",
                        (workspace_id, point_id, str(page["content_markdown"]), revision, updated_at),
                    )
                else:
                    self._conn.execute(
                        """UPDATE gateway_knowledge_pages
                           SET draft_markdown=?,revision=?,updated_at=?
                           WHERE workspace_id=? AND knowledge_point_id=?""",
                        (str(page["content_markdown"]), revision, updated_at, workspace_id, point_id),
                    )

            for asset in assets:
                content = bytes(asset["content"])
                self._conn.execute(
                    """INSERT INTO gateway_knowledge_book_assets(
                       workspace_id,asset_path,media_type,draft_content,size_bytes,sha256,updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(workspace_id,asset_path) DO UPDATE SET
                         media_type=excluded.media_type,draft_content=excluded.draft_content,
                         size_bytes=excluded.size_bytes,sha256=excluded.sha256,
                         updated_at=excluded.updated_at""",
                    (
                        workspace_id,
                        str(asset["asset_path"]),
                        str(asset["media_type"]),
                        content,
                        len(content),
                        str(asset["sha256"]),
                        _now(),
                    ),
                )
        return [self.get_knowledge_page(workspace_id, str(page["knowledge_point_id"])) for page in pages]  # type: ignore[list-item]

    def get_knowledge_book_asset(self, workspace_id: str, asset_path: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT workspace_id,asset_path,media_type,published_content AS content,size_bytes,sha256
                   FROM gateway_knowledge_book_assets
                   WHERE workspace_id=? AND asset_path=?""",
                (workspace_id, asset_path),
            ).fetchone()
        if row is None or row["content"] is None:
            return None
        return dict(row)

    def select_guided_blueprint(self, *, workspace_id: str, topic_id: str) -> dict[str, Any] | None:
        catalog = self.get_teaching_catalog(workspace_id)["catalog"]
        topic = next((item for item in catalog.get("topics", []) if item.get("id") == topic_id), None)
        if topic is None or topic.get("status", "enabled") != "enabled":
            return None
        enabled_points = {point.get("id") for point in topic.get("knowledge_points", []) if point.get("status", "enabled") == "enabled"}
        with self._lock:
            rows = self._conn.execute(
                """SELECT blueprint_json FROM gateway_blueprints
                   WHERE workspace_id=? AND kind='guided' AND topic_id=? AND status='enabled'""",
                (workspace_id, topic_id),
            ).fetchall()
        candidates = [item for item in (json.loads(row["blueprint_json"]) for row in rows) if item.get("knowledge_point_id") in enabled_points]
        return random.choice(candidates) if candidates else None

    def start_exercise_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
        user_id: str,
        topic_id: str,
        mode: str,
    ) -> dict[str, Any] | None:
        if mode not in {"practice", "review"}:
            raise ValueError("exercise session mode must be practice or review")
        catalog = self.get_teaching_catalog(workspace_id)["catalog"]
        topic = next((item for item in catalog.get("topics", []) if item.get("id") == topic_id), None)
        if topic is None or topic.get("status", "enabled") != "enabled":
            return None
        with self._lock:
            rows = self._conn.execute(
                """SELECT blueprint_json FROM gateway_blueprints
                   WHERE workspace_id=? AND kind=? AND topic_id=? AND status='enabled'""",
                (workspace_id, "exercise" if mode == "practice" else "review", topic_id),
            ).fetchall()
        enabled_points = {point.get("id") for point in topic.get("knowledge_points", []) if point.get("status", "enabled") == "enabled"}
        candidates = [
            item for item in (json.loads(row["blueprint_json"]) for row in rows)
            if item.get("knowledge_point_id", "legacy_unassigned") == "legacy_unassigned"
            or item.get("knowledge_point_id") in enabled_points
        ]
        if not candidates:
            return None
        now = _now()
        record = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "topic_id": topic_id,
            "mode": mode,
            "status": "active",
            "blueprint_snapshot": random.choice(candidates),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO gateway_exercise_sessions(
                    id,session_id,workspace_id,user_id,topic_id,mode,status,blueprint_snapshot_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["id"], record["session_id"], record["workspace_id"], record["user_id"],
                    record["topic_id"], record["mode"], record["status"],
                    json.dumps(record["blueprint_snapshot"], ensure_ascii=False, separators=(",", ":")),
                    now, now,
                ),
            )
        return record

    def get_exercise_session(self, exercise_session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_exercise_sessions WHERE id=?", (exercise_session_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["blueprint_snapshot"] = json.loads(value.pop("blueprint_snapshot_json"))
        return value

    def active_or_latest_exercise_session(
        self, *, session_id: str, topic_id: str, mode: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM gateway_exercise_sessions
                   WHERE session_id=? AND topic_id=? AND mode=?
                   ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC LIMIT 1""",
                (session_id, topic_id, mode),
            ).fetchone()
        return self.get_exercise_session(str(row["id"])) if row is not None else None

    def _exercise_question(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["rubric"] = json.loads(value.pop("rubric_json"))
        return value

    def exercise_questions(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM gateway_exercise_questions WHERE exercise_session_id=? ORDER BY sequence",
                (exercise_session_id,),
            ).fetchall()
        return [self._exercise_question(row) for row in rows]

    def exercise_attempts(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.* FROM gateway_exercise_attempts a
                   JOIN gateway_exercise_questions q ON q.id=a.exercise_question_id
                   WHERE q.exercise_session_id=? ORDER BY q.sequence, a.attempt_number""",
                (exercise_session_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["rubric_matches"] = json.loads(value.pop("rubric_matches_json"))
            value["passed"] = bool(value["passed"])
            values.append(value)
        return values

    def learning_evidence(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM gateway_learning_evidence WHERE exercise_session_id=? ORDER BY completed_at, id",
                (exercise_session_id,),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["blueprint_snapshot"] = json.loads(value.pop("blueprint_snapshot_json"))
            value["rubric_matches"] = json.loads(value.pop("rubric_matches_json"))
            value["knowledge_point_ids"] = json.loads(value.pop("knowledge_point_ids_json"))
            value["passed"] = bool(value["passed"])
            values.append(value)
        return values

    def exercise_state(self, exercise_session_id: str) -> ExerciseState:
        session = self.get_exercise_session(exercise_session_id)
        if session is None:
            return ExerciseState()
        blueprint = session["blueprint_snapshot"]
        questions = self.exercise_questions(exercise_session_id)
        current = next((item for item in reversed(questions) if item["status"] == "awaiting_answer"), None)
        count = 1
        rubric = [str(point.get("criterion", point)) if isinstance(point, dict) else str(point) for point in blueprint.get("rubric", [])]
        if session["status"] != "active":
            attempts = len(self.exercise_attempts(exercise_session_id))
            return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id,
                                 rubric=rubric, question_number=len(questions), question_count=count,
                                 attempt=attempts, status="completed")
        if current is None:
            return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id,
                                 rubric=rubric, question_number=len(questions) + 1, question_count=count, status="idle")
        attempts = sum(1 for attempt in self.exercise_attempts(exercise_session_id) if attempt["exercise_question_id"] == current["id"])
        return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id,
                             question=current["question"], rubric=rubric, attempt=attempts,
                             question_number=int(current["sequence"]), question_count=count, status="awaiting_answer")

    def record_exercise_question(self, exercise_session_id: str, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("exercise question must not be empty")
        session = self.get_exercise_session(exercise_session_id)
        if session is None or session["status"] != "active":
            raise ValueError("exercise session is not active")
        existing = self.exercise_questions(exercise_session_id)
        if any(item["status"] == "awaiting_answer" for item in existing):
            raise ValueError("exercise already has an unanswered question")
        limit = 1
        if len(existing) >= limit:
            raise ValueError("exercise question count is complete")
        now = _now()
        record = {"id": str(uuid.uuid4()), "exercise_session_id": exercise_session_id,
                  "sequence": len(existing) + 1, "question": question,
                  "rubric": session["blueprint_snapshot"].get("rubric", []), "status": "awaiting_answer",
                  "created_at": now, "completed_at": None}
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO gateway_exercise_questions(id,exercise_session_id,sequence,question,rubric_json,status,created_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (record["id"], exercise_session_id, record["sequence"], question,
                 json.dumps(record["rubric"], ensure_ascii=False), "awaiting_answer", now, None),
            )
            self._conn.execute("UPDATE gateway_exercise_sessions SET updated_at=? WHERE id=?", (now, exercise_session_id))
        return record

    def grade_exercise_answer(
        self, exercise_session_id: str, *, answer: str, matches: list[dict[str, Any]], feedback: str,
    ) -> ExerciseState:
        session = self.get_exercise_session(exercise_session_id)
        if session is None or session["status"] != "active":
            raise ValueError("exercise session is not active")
        questions = self.exercise_questions(exercise_session_id)
        question = next((item for item in reversed(questions) if item["status"] == "awaiting_answer"), None)
        if question is None:
            raise ValueError("exercise has no unanswered question")
        rubric = question["rubric"]
        indexes = {item.get("criterion_index") for item in matches if isinstance(item, dict)}
        if indexes != set(range(len(rubric))) or len(matches) != len(rubric):
            raise ValueError("grading result does not match blueprint rubric")
        weights = [max(0.0, float(point.get("weight", 1) if isinstance(point, dict) else 1)) for point in rubric]
        total_weight = sum(weights) or float(len(weights) or 1)
        normalized_score = round(sum(weights[int(item["criterion_index"])] for item in matches if bool(item.get("achieved"))) / total_weight * 100)
        passed = normalized_score >= 60
        normalized_matches = [
            {"criterion_index": int(item["criterion_index"]), "criterion": str(rubric[int(item["criterion_index"])].get("criterion", "") if isinstance(rubric[int(item["criterion_index"])], dict) else rubric[int(item["criterion_index"])]),
             "weight": weights[int(item["criterion_index"])], "achieved": bool(item.get("achieved")), "evidence": str(item.get("evidence", ""))[:1000]}
            for item in sorted(matches, key=lambda value: int(value["criterion_index"]))
        ]
        attempt_number = sum(1 for item in self.exercise_attempts(exercise_session_id) if item["exercise_question_id"] == question["id"]) + 1
        is_last = True
        now = _now()
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO gateway_exercise_attempts(id,exercise_question_id,answer,attempt_number,rubric_matches_json,normalized_score,passed,feedback,created_at)
                                VALUES (?,?,?,?,?,?,?,?,?)""",
                               (str(uuid.uuid4()), question["id"], answer, attempt_number, json.dumps(normalized_matches, ensure_ascii=False), normalized_score, int(passed), feedback[:4000], now))
            self._conn.execute("UPDATE gateway_exercise_questions SET status='completed', completed_at=? WHERE id=?", (now, question["id"]))
            self._conn.execute("""INSERT INTO gateway_learning_evidence(id,exercise_session_id,exercise_question_id,blueprint_snapshot_json,question,learner_answer,attempt_number,rubric_matches_json,normalized_score,passed,knowledge_point_ids_json,completed_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (str(uuid.uuid4()), exercise_session_id, question["id"], json.dumps(session["blueprint_snapshot"], ensure_ascii=False), question["question"], answer, attempt_number, json.dumps(normalized_matches, ensure_ascii=False), normalized_score, int(passed), json.dumps([session["blueprint_snapshot"]["knowledge_point_id"]] if session["blueprint_snapshot"].get("knowledge_point_id") else session["blueprint_snapshot"].get("knowledge_point_ids", []), ensure_ascii=False), now))
            self._conn.execute("UPDATE gateway_exercise_sessions SET status='completed', updated_at=?, completed_at=? WHERE id=?", (now, now, exercise_session_id))
        return self.exercise_state(exercise_session_id)

    def end_exercise_sessions(self, *, session_id: str, status: str = "cancelled") -> int:
        if status not in {"completed", "cancelled", "expired"}:
            raise ValueError("invalid exercise session terminal status")
        now = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE gateway_exercise_sessions SET status=?, updated_at=?, completed_at=?
                   WHERE session_id=? AND status='active'""", (status, now, now, session_id)
            )
        return cursor.rowcount

    def expire_exercise_sessions(self, *, session_id: str, idle_minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).isoformat()
        now = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE gateway_exercise_sessions SET status='expired', updated_at=?, completed_at=?
                   WHERE session_id=? AND status='active' AND updated_at<?""", (now, now, session_id, cutoff)
            )
        return cursor.rowcount

    def prune_events(
        self,
        *,
        retention_days: int,
        max_events_per_session: int,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Compact terminal-turn streams without touching active turn events."""
        cutoff = (
            (now or datetime.now(timezone.utc)) - timedelta(days=max(1, retention_days))
        ).isoformat()
        terminal_statuses = (
            TurnStatus.COMPLETED.value,
            TurnStatus.FAILED.value,
            TurnStatus.CANCELLED.value,
            TurnStatus.INTERRUPTED.value,
        )
        retained_types = (
            GatewayEventType.MESSAGE_COMPLETED.value,
            GatewayEventType.TURN_COMPLETED.value,
            GatewayEventType.TURN_FAILED.value,
            GatewayEventType.TURN_CANCELLED.value,
        )
        with self._lock, self._conn:
            compacted = self._conn.execute(
                """DELETE FROM gateway_events
                   WHERE created_at < ?
                     AND event_type NOT IN (?,?,?,?)
                     AND turn_id IN (
                       SELECT turn_id FROM gateway_turns WHERE status IN (?,?,?,?)
                     )""",
                (cutoff, *retained_types, *terminal_statuses),
            ).rowcount
            session_rows = self._conn.execute(
                "SELECT DISTINCT session_id FROM gateway_events"
            ).fetchall()
            capped = 0
            for row in session_rows:
                capped += self._conn.execute(
                    """DELETE FROM gateway_events WHERE event_id IN (
                         SELECT e.event_id
                         FROM gateway_events e
                         JOIN gateway_turns t ON t.turn_id=e.turn_id
                         WHERE e.session_id=? AND t.status IN (?,?,?,?)
                         ORDER BY e.created_at DESC, e.sequence DESC, e.event_id DESC
                         LIMIT -1 OFFSET ?
                       )""",
                    (
                        row["session_id"],
                        *terminal_statuses,
                        max(1, max_events_per_session),
                    ),
                ).rowcount
            remaining = int(
                self._conn.execute("SELECT COUNT(*) FROM gateway_events").fetchone()[0]
            )
        return {
            "compacted": max(0, compacted),
            "capped": max(0, capped),
            "remaining": remaining,
        }

    def recover_interrupted(self) -> list[TurnRecord]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT turn_id FROM gateway_turns WHERE status IN (?,?)",
                (TurnStatus.ACCEPTED.value, TurnStatus.RUNNING.value),
            ).fetchall()
            recovered = []
            for row in rows:
                recovered.append(
                    self.update_turn(
                        row["turn_id"],
                        TurnStatus.INTERRUPTED,
                        error_kind="gateway_restart",
                        error_message="Gateway restarted before the turn completed.",
                    )
                )
            return recovered

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """DELETE FROM gateway_learning_evidence WHERE exercise_session_id IN (
                   SELECT id FROM gateway_exercise_sessions WHERE session_id=?)""", (session_id,)
            )
            self._conn.execute(
                """DELETE FROM gateway_exercise_attempts WHERE exercise_question_id IN (
                   SELECT q.id FROM gateway_exercise_questions q
                   JOIN gateway_exercise_sessions s ON s.id=q.exercise_session_id WHERE s.session_id=?)""", (session_id,)
            )
            self._conn.execute(
                """DELETE FROM gateway_exercise_questions WHERE exercise_session_id IN (
                   SELECT id FROM gateway_exercise_sessions WHERE session_id=?)""", (session_id,)
            )
            self._conn.execute("DELETE FROM gateway_exercise_sessions WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM gateway_guided_sessions WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM gateway_turns WHERE session_id=?", (session_id,))

    def clear_learning_sessions(self) -> dict[str, int]:
        """Clear learner runtime data without touching settings or teacher catalogues."""
        with self._lock, self._conn:
            counts = {
                table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "gateway_turns", "gateway_events", "gateway_exercise_sessions",
                    "gateway_exercise_questions", "gateway_exercise_attempts", "gateway_learning_evidence",
                )
            }
            self._conn.execute("DELETE FROM gateway_learning_evidence")
            self._conn.execute("DELETE FROM gateway_exercise_attempts")
            self._conn.execute("DELETE FROM gateway_exercise_questions")
            self._conn.execute("DELETE FROM gateway_exercise_sessions")
            self._conn.execute("DELETE FROM gateway_events")
            self._conn.execute("DELETE FROM gateway_turns")
        return counts

    def health(self) -> dict[str, Any]:
        with self._lock:
            events = self._conn.execute("SELECT COUNT(*) FROM gateway_events").fetchone()[0]
        return {"database": str(self.path), "durable_events": int(events)}

    def flush(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _turn(row: sqlite3.Row) -> TurnRecord:
        return TurnRecord(
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            status=row["status"],
            input_text=row["input_text"],
            learning_context=(LearningContext.model_validate_json(row["learning_context_json"])
                              if row["learning_context_json"] else None),
            learning_progress=(LearningProgress.model_validate_json(row["learning_progress_json"])
                               if row["learning_progress_json"] else None),
            guided_session=(GuidedSessionRef(
                id=row["guided_session_id"],
                blueprint_id=row["guided_blueprint_id"],
                blueprint_snapshot_sha256=row["guided_blueprint_snapshot_sha256"],
                attempts=int(row["guided_session_attempts"] or 0),
                status=str(row["guided_session_status"] or "active"),
            ) if row["guided_session_id"] else None),
            exercise_state=(ExerciseState.model_validate_json(row["exercise_state_json"])
                            if row["exercise_state_json"] else None),
            final_text=row["final_text"],
            error_kind=row["error_kind"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> GatewayEvent:
        return GatewayEvent(
            event_id=row["event_id"],
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            sequence=row["sequence"],
            type=row["event_type"],
            created_at=row["created_at"],
            payload=json.loads(row["payload_json"] or "{}"),
        )
