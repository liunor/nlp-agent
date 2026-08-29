"""Synchronous GatewayRepository-compatible facade backed exclusively by MySQL.

The Gateway calls repository methods from worker threads, so this facade owns a
short SQLAlchemy transaction per command while the schema remains Alembic-only.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from core.learning import ExerciseState, LearningContext, LearningProgress, knowledge_point_ids
from gateway.contracts import (
    GatewayEvent,
    GatewayEventType,
    KnowledgeBookRevisionConflictError,
    TeachingConfigurationError,
    TurnRecord,
    TurnStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MySQLGatewayRepository:
    def __init__(self, url: str, *, knowledge_point_prompt_budget: int = 12_000) -> None:
        if not url.startswith("mysql+aiomysql://"):
            raise ValueError("MySQL Gateway repository requires mysql+aiomysql DSN")
        self._engine = create_engine(url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
        self.knowledge_point_prompt_budget = max(1, knowledge_point_prompt_budget)

    def _row(self, turn_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as c:
            row = c.execute(text("SELECT * FROM nlp_turns WHERE id=:id"), {"id": turn_id}).mappings().first()
            return dict(row) if row else None

    def _record(self, row: dict[str, Any]) -> TurnRecord:
        state = self._json(row.get("learning_state_json") or {})
        return TurnRecord(turn_id=row["id"], session_id=row["conversation_id"], workspace_id=row["workspace_id"], user_id=row["user_id"], status=TurnStatus(row["status"]), input_text=row["input_text"], learning_context=LearningContext.model_validate(state["context"]) if state.get("context") else None, learning_progress=LearningProgress.model_validate(state["progress"]) if state.get("progress") else None, exercise_state=ExerciseState.model_validate(state["exercise"]) if state.get("exercise") else None, final_text=row.get("result_text"), error_kind=row.get("error_kind"), error_message=row.get("error_message"), created_at=row["created_at"], started_at=row.get("started_at"), completed_at=row.get("completed_at"))

    @staticmethod
    def _ensure_conversation(
        connection: Connection,
        *,
        session_id: str,
        workspace_id: str,
        user_id: str,
        title: str,
    ) -> None:
        existing = connection.execute(
            text(
                "SELECT workspace_id,owner_user_id FROM nlp_conversations "
                "WHERE id=:id FOR UPDATE"
            ),
            {"id": session_id},
        ).mappings().first()
        if existing is not None and (
            existing["workspace_id"] != workspace_id
            or existing["owner_user_id"] != user_id
        ):
            raise PermissionError("conversation belongs to another principal")
        connection.execute(
            text(
                "INSERT INTO nlp_conversations(id,workspace_id,owner_user_id,title,status) "
                "VALUES(:id,:workspace,:user,:title,'active') "
                "ON DUPLICATE KEY UPDATE id=VALUES(id)"
            ),
            {
                "id": session_id,
                "workspace": workspace_id,
                "user": user_id,
                "title": title[:255],
            },
        )
        identity = connection.execute(
            text(
                "SELECT workspace_id,owner_user_id FROM nlp_conversations "
                "WHERE id=:id"
            ),
            {"id": session_id},
        ).mappings().one()
        if identity["workspace_id"] != workspace_id or identity["owner_user_id"] != user_id:
            raise PermissionError("conversation identity does not match the current session")

    def create_turn(self, *, turn_id: str, session_id: str, workspace_id: str, user_id: str, input_text: str, idempotency_key: str | None, learning_context=None, learning_progress=None, exercise_state=None, dispatch_payload: str | None = None, **_: Any) -> tuple[TurnRecord, bool]:
        with self._engine.begin() as c:
            self._ensure_conversation(
                c,
                session_id=session_id,
                workspace_id=workspace_id,
                user_id=user_id,
                title=input_text,
            )
            if idempotency_key:
                old = c.execute(text("SELECT * FROM nlp_turns WHERE user_id=:u AND conversation_id=:s AND idempotency_key=:k"), {"u": user_id, "s": session_id, "k": idempotency_key}).mappings().first()
                if old:
                    return self._record(dict(old)), True
            state = {"context": learning_context.model_dump(mode="json") if learning_context else None, "progress": learning_progress.model_dump(mode="json") if learning_progress else None, "exercise": exercise_state.model_dump(mode="json") if exercise_state else None}
            c.execute(text("INSERT INTO nlp_turns(id,conversation_id,workspace_id,user_id,status,input_text,learning_state_json,idempotency_key) VALUES(:id,:s,:w,:u,'accepted',:input,:state,:key)"), {"id": turn_id, "s": session_id, "w": workspace_id, "u": user_id, "input": input_text, "state": json.dumps(state), "key": idempotency_key})
            if dispatch_payload is not None:
                c.execute(text("INSERT INTO nlp_outbox_messages(id,topic,payload_json,status) VALUES(UUID(),'turn.dispatch',:payload,'pending')"), {"payload": json.dumps({"turn_id": turn_id, "task": dispatch_payload})})
        return self._record(self._row(turn_id) or {}), False

    def update_turn(self, turn_id: str, status: TurnStatus, *, final_text=None, error_kind=None, error_message=None, exercise_state=None, dispatch_payload: str | None = None) -> TurnRecord:
        terminal = status in {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.INTERRUPTED}
        with self._engine.begin() as c:
            c.execute(text("UPDATE nlp_turns SET status=:status,result_text=:result,error_kind=:kind,error_message=:message,started_at=CASE WHEN :running='running' THEN UTC_TIMESTAMP(6) ELSE started_at END,completed_at=CASE WHEN :terminal=1 THEN UTC_TIMESTAMP(6) ELSE completed_at END WHERE id=:id"), {"status": status.value, "result": final_text, "kind": error_kind, "message": (error_message or "")[:1000] or None, "running": status.value, "terminal": int(terminal), "id": turn_id})
            if dispatch_payload is not None:
                c.execute(text("INSERT INTO nlp_outbox_messages(id,topic,payload_json,status) VALUES(UUID(),'turn.dispatch',:payload,'pending')"), {"payload": json.dumps({"turn_id": turn_id, "task": dispatch_payload})})
        row = self._row(turn_id)
        if row is None:
            raise KeyError(turn_id)
        return self._record(row)

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        row = self._row(turn_id)
        return self._record(row) if row else None

    def request_turn_cancellation(
        self, *, turn_id: str, requested_by: str, reason: str = "user_requested"
    ) -> TurnRecord | None:
        """Record cancellation in MySQL before any transport-level signal."""
        with self._engine.begin() as c:
            row = c.execute(
                text("SELECT status FROM nlp_turns WHERE id=:id FOR UPDATE"),
                {"id": turn_id},
            ).mappings().first()
            if row is None:
                return None
            c.execute(
                text(
                    "INSERT INTO nlp_turn_cancellations(turn_id,requested_by,reason) "
                    "VALUES(:turn_id,:requested_by,:reason) "
                    "ON DUPLICATE KEY UPDATE turn_id=VALUES(turn_id)"
                ),
                {
                    "turn_id": turn_id,
                    "requested_by": requested_by,
                    "reason": reason[:500],
                },
            )
            if row["status"] in {"accepted", "running"}:
                c.execute(
                    text(
                        "UPDATE nlp_turns SET status='cancelled', "
                        "completed_at=UTC_TIMESTAMP(6) WHERE id=:id"
                    ),
                    {"id": turn_id},
                )
        return self.get_turn(turn_id)

    def active_turn_for_session(self, session_id: str, *, exclude_turn_id: str | None = None) -> TurnRecord | None:
        with self._engine.connect() as c:
            row = c.execute(text("SELECT * FROM nlp_turns WHERE conversation_id=:s AND status IN ('accepted','running') AND (:exclude IS NULL OR id<>:exclude) ORDER BY created_at DESC LIMIT 1"), {"s": session_id, "exclude": exclude_turn_id}).mappings().first()
        return self._record(dict(row)) if row else None

    def turn_for_idempotency(self, *, user_id: str, session_id: str, idempotency_key: str) -> TurnRecord | None:
        with self._engine.connect() as c:
            row = c.execute(text("SELECT * FROM nlp_turns WHERE user_id=:u AND conversation_id=:s AND idempotency_key=:k"), {"u": user_id, "s": session_id, "k": idempotency_key}).mappings().first()
        return self._record(dict(row)) if row else None

    def append_event(self, *, turn_id: str, session_id: str, event_type: GatewayEventType, payload=None) -> GatewayEvent:
        with self._engine.begin() as c:
            c.execute(text("SELECT id FROM nlp_turns WHERE id=:id FOR UPDATE"), {"id": turn_id})
            sequence = int(c.execute(text("SELECT COALESCE(MAX(sequence),0)+1 FROM nlp_turn_events WHERE turn_id=:id"), {"id": turn_id}).scalar_one())
            event_id = str(uuid.uuid4())
            c.execute(text("INSERT INTO nlp_turn_events(id,turn_id,sequence,claim_generation,event_type,payload_json) SELECT :event,:turn,:seq,claim_generation,:type,:payload FROM nlp_turns WHERE id=:turn"), {"event": event_id, "turn": turn_id, "seq": sequence, "type": event_type.value, "payload": json.dumps(payload or {})})
        return GatewayEvent(event_id=event_id, turn_id=turn_id, session_id=session_id, sequence=sequence, type=event_type, payload=payload or {})

    def events_after(self, turn_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[GatewayEvent]:
        with self._engine.connect() as c:
            rows = c.execute(text("SELECT e.*, t.conversation_id FROM nlp_turn_events e JOIN nlp_turns t ON t.id=e.turn_id WHERE e.turn_id=:id AND e.sequence>:after ORDER BY e.sequence LIMIT :limit"), {"id": turn_id, "after": max(0, after_sequence), "limit": min(max(1, limit), 2000)}).mappings().all()
        return [GatewayEvent(event_id=r["id"], turn_id=turn_id, session_id=r["conversation_id"], sequence=r["sequence"], type=GatewayEventType(r["event_type"]), created_at=r["created_at"], payload=self._json(r["payload_json"])) for r in rows]

    def ensure_event(self, *, turn_id: str, session_id: str, event_type: GatewayEventType, payload=None) -> GatewayEvent:
        existing = next((e for e in self.events_after(turn_id, limit=2000) if e.type == event_type), None)
        return existing or self.append_event(turn_id=turn_id, session_id=session_id, event_type=event_type, payload=payload)

    def list_turns(self, session_id: str, *, limit: int = 100) -> list[TurnRecord]:
        with self._engine.connect() as c:
            rows = c.execute(text("SELECT * FROM nlp_turns WHERE conversation_id=:s ORDER BY created_at DESC LIMIT :limit"), {"s": session_id, "limit": min(max(1, limit), 500)}).mappings().all()
        return [self._record(dict(r)) for r in rows]

    def list_question_turns(self, *, workspace_id: str, since: str) -> list[dict[str, Any]]:
        """Teacher read model: structured question rows for analytics.

        Deliberately omits ``input_text`` so teacher analytics can only report
        aggregates, never the raw student question text.  The time window is the
        only bound: aggregation must reflect every turn, not a sampled prefix.
        """
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT conversation_id,user_id,error_kind,created_at,learning_state_json "
                    "FROM nlp_turns WHERE workspace_id=:w AND created_at>=:since "
                    "ORDER BY created_at"
                ),
                {"w": workspace_id, "since": since},
            ).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            context = (self._json(row["learning_state_json"] or {}) or {}).get("context") or {}
            created = row["created_at"]
            day = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else str(created)[:10]
            result.append(
                {
                    "session_id": row["conversation_id"],
                    "user_id": row["user_id"],
                    "has_error": bool(row["error_kind"]),
                    "topic_id": context.get("topic_id"),
                    "level": context.get("level"),
                    "mode": context.get("mode"),
                    "day": day,
                }
            )
        return result

    def exercise_evidence_stats(self, *, workspace_id: str, since: str, limit: int = 10_000) -> list[dict[str, Any]]:
        """Teacher read model: one row per graded exercise item with topic/knowledge-point refs."""
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT e.normalized_score,e.passed,e.blueprint_snapshot_json,s.topic_id,s.mode "
                    "FROM nlp_learning_evidence e "
                    "JOIN nlp_exercise_sessions s ON s.id=e.exercise_session_id "
                    "WHERE s.workspace_id=:w AND s.completed_at>=:since "
                    "ORDER BY s.completed_at DESC LIMIT :limit"
                ),
                {"w": workspace_id, "since": since, "limit": min(max(1, limit), 10_000)},
            ).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            blueprint = self._json(row["blueprint_snapshot_json"] or {})
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "mode": row["mode"],
                    "knowledge_point_ids": knowledge_point_ids(blueprint),
                    "score": int(row["normalized_score"]),
                    "passed": bool(row["passed"]),
                }
            )
        return result

    def exercise_criterion_stats(self, *, workspace_id: str, since: str, limit: int = 20_000) -> list[dict[str, Any]]:
        """Teacher read model: per-attempt rubric matches for criterion hit-rate aggregation."""
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT a.rubric_matches_json,s.topic_id,s.blueprint_snapshot_json "
                    "FROM nlp_exercise_attempts a "
                    "JOIN nlp_exercise_questions q ON q.id=a.exercise_question_id "
                    "JOIN nlp_exercise_sessions s ON s.id=q.exercise_session_id "
                    "WHERE s.workspace_id=:w AND s.completed_at>=:since "
                    "ORDER BY s.completed_at DESC LIMIT :limit"
                ),
                {"w": workspace_id, "since": since, "limit": min(max(1, limit), 50_000)},
            ).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            blueprint = self._json(row["blueprint_snapshot_json"] or {})
            matches = self._json(row["rubric_matches_json"] or {})
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "knowledge_point_ids": knowledge_point_ids(blueprint),
                    "matches": [m for m in matches if isinstance(m, dict)],
                }
            )
        return result

    def guided_session_stats(self, *, workspace_id: str, since: str, limit: int = 10_000) -> list[dict[str, Any]]:
        """Teacher read model: misconception counts per guided session (active + recent completed)."""
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT topic_id,state_json FROM nlp_guided_sessions "
                    "WHERE workspace_id=:w AND (completed_at>=:since OR completed_at IS NULL) "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"w": workspace_id, "since": since, "limit": min(max(1, limit), 10_000)},
            ).mappings().all()
        result: list[dict[str, Any]] = []
        for row in rows:
            state = self._json(row["state_json"] or {}) or {}
            misconceptions = state.get("misconceptions") or []
            result.append(
                {
                    "topic_id": row["topic_id"],
                    "misconception_count": len(misconceptions) if isinstance(misconceptions, list) else 0,
                }
            )
        return result

    def latest_learning_state(self, session_id: str):
        turns = [t for t in self.list_turns(session_id) if not (t.status == TurnStatus.FAILED and t.error_kind == "turn_conflict")]
        latest = turns[0] if turns else None
        return (latest.learning_context, latest.learning_progress, latest.exercise_state) if latest else (None, None, None)

    @staticmethod
    def _json(value: Any) -> Any:
        return value if isinstance(value, (dict, list)) else (json.loads(value) if value else {})

    def _guided(self, row: Any) -> dict[str, Any]:
        state = self._json(row["state_json"])
        return {"id": row["id"], "session_id": row["conversation_id"], "workspace_id": row["workspace_id"], "user_id": row["user_id"], "topic_id": row["topic_id"], "status": row["status"], "objective": row["objective"], "attempts": int(row["attempts"]), "guided_blueprint": self._json(row["blueprint_snapshot_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"], **state}

    def active_guided_session(self, *, session_id: str, topic_id: str):
        with self._engine.connect() as c:
            row = c.execute(text("SELECT * FROM nlp_guided_sessions WHERE conversation_id=:s AND topic_id=:t AND status='active' ORDER BY created_at DESC LIMIT 1"), {"s": session_id, "t": topic_id}).mappings().first()
        return self._guided(row) if row else None

    def start_or_resume_guided_session(self, *, session_id: str, workspace_id: str, user_id: str, topic_id: str, first_message: str, guided_blueprint=None):
        with self._engine.begin() as c:
            row = c.execute(text("SELECT * FROM nlp_guided_sessions WHERE conversation_id=:s AND topic_id=:t AND status='active' ORDER BY created_at DESC LIMIT 1 FOR UPDATE"), {"s": session_id, "t": topic_id}).mappings().first()
            if row:
                state = self._json(row["state_json"])
                state["learner_responses"] = [*state.get("learner_responses", []), first_message][:20]
                c.execute(text("UPDATE nlp_guided_sessions SET attempts=attempts+1,state_json=:state WHERE id=:id"), {"id": row["id"], "state": json.dumps(state, ensure_ascii=False)})
            else:
                record_id = str(uuid.uuid4())
                c.execute(text("INSERT INTO nlp_guided_sessions(id,conversation_id,workspace_id,user_id,topic_id,status,objective,attempts,state_json,blueprint_snapshot_json) VALUES(:id,:s,:w,:u,:t,'active',:objective,0,:state,:blueprint)"), {"id": record_id, "s": session_id, "w": workspace_id, "u": user_id, "t": topic_id, "objective": first_message, "state": json.dumps({"stage": "opening", "learner_responses": [], "known_concepts": [], "misconceptions": [], "last_question": ""}, ensure_ascii=False), "blueprint": json.dumps(guided_blueprint or {}, ensure_ascii=False)})
        return self.active_guided_session(session_id=session_id, topic_id=topic_id) or {}

    def advance_guided_session(self, guided_session_id: str, *, tutor_message: str, known_concepts: object = None, misconceptions: object = None, completed: bool = False):
        known = [str(item)[:300] for item in known_concepts if str(item).strip()][:30] if isinstance(known_concepts, list) else []
        mistakes = [str(item)[:300] for item in misconceptions if str(item).strip()][:20] if isinstance(misconceptions, list) else []
        with self._engine.begin() as c:
            row = c.execute(text("SELECT state_json FROM nlp_guided_sessions WHERE id=:id AND status='active' FOR UPDATE"), {"id": guided_session_id}).mappings().first()
            if row is None: return None
            state = self._json(row["state_json"])
            state.update({"stage": "completed" if completed else "awaiting_learner_response", "known_concepts": known, "misconceptions": mistakes, "last_question": tutor_message[:2000]})
            c.execute(text("UPDATE nlp_guided_sessions SET status=:status,state_json=:state,completed_at=CASE WHEN :completed THEN UTC_TIMESTAMP(6) ELSE NULL END WHERE id=:id"), {"id": guided_session_id, "status": "completed" if completed else "active", "state": json.dumps(state, ensure_ascii=False), "completed": completed})
    def update_turn_guided_status(self, turn_id: str, *, status: str) -> None:
        with self._engine.begin() as c:
            c.execute(text("UPDATE nlp_turns SET learning_state_json=JSON_SET(COALESCE(learning_state_json, JSON_OBJECT()), '$.guided_session_status', :status) WHERE id=:id"), {"id": turn_id, "status": status})
    def end_guided_sessions(self, *, session_id: str, status: str = "cancelled") -> int:
        if status not in {"completed", "cancelled", "expired"}: raise ValueError("invalid guided session terminal status")
        with self._engine.begin() as c:
            return c.execute(text("UPDATE nlp_guided_sessions SET status=:status,completed_at=UTC_TIMESTAMP(6) WHERE conversation_id=:s AND status='active'"), {"s": session_id, "status": status}).rowcount
    def expire_guided_sessions(self, *, session_id: str, idle_minutes: int = 30) -> int:
        with self._engine.begin() as c:
            return c.execute(text("UPDATE nlp_guided_sessions SET status='expired',completed_at=UTC_TIMESTAMP(6) WHERE conversation_id=:s AND status='active' AND updated_at < :cutoff"), {"s": session_id, "cutoff": _now() - timedelta(minutes=max(1, idle_minutes))}).rowcount

    def _exercise(self, row: Any) -> dict[str, Any]:
        return {"id": row["id"], "session_id": row["conversation_id"], "workspace_id": row["workspace_id"], "user_id": row["user_id"], "topic_id": row["topic_id"], "mode": row["mode"], "status": row["status"], "blueprint_snapshot": self._json(row["blueprint_snapshot_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"]}

    def get_exercise_session(self, exercise_session_id: str):
        with self._engine.connect() as c: row = c.execute(text("SELECT * FROM nlp_exercise_sessions WHERE id=:id"), {"id": exercise_session_id}).mappings().first()
        return self._exercise(row) if row else None

    def active_exercise_session(self, *, session_id: str, topic_id: str, mode: str):
        with self._engine.connect() as c: row = c.execute(text("SELECT * FROM nlp_exercise_sessions WHERE conversation_id=:s AND topic_id=:t AND mode=:m AND status='active' ORDER BY created_at DESC LIMIT 1"), {"s": session_id, "t": topic_id, "m": mode}).mappings().first()
        return self._exercise(row) if row else None

    def active_or_latest_exercise_session(self, *, session_id: str, topic_id: str, mode: str):
        with self._engine.connect() as c: row = c.execute(text("SELECT * FROM nlp_exercise_sessions WHERE conversation_id=:s AND topic_id=:t AND mode=:m ORDER BY (status='active') DESC,created_at DESC LIMIT 1"), {"s": session_id, "t": topic_id, "m": mode}).mappings().first()
        return self._exercise(row) if row else None

    def start_exercise_session(self, *, session_id: str, workspace_id: str, user_id: str, topic_id: str, mode: str):
        if mode not in {"practice", "review"}: raise ValueError("exercise session mode must be practice or review")
        catalog = self.get_teaching_catalog(workspace_id)["catalog"]
        topic = next((item for item in catalog["topics"] if item["id"] == topic_id and item.get("status", "enabled") == "enabled"), None)
        candidates = [item for item in catalog["exercise_blueprints" if mode == "practice" else "review_blueprints"] if item.get("topic_id") == topic_id and item.get("status") == "enabled"] if topic else []
        if not candidates: return None
        record_id = str(uuid.uuid4())
        with self._engine.begin() as c: c.execute(text("INSERT INTO nlp_exercise_sessions(id,conversation_id,workspace_id,user_id,topic_id,mode,status,blueprint_snapshot_json) VALUES(:id,:s,:w,:u,:t,:m,'active',:blueprint)"), {"id": record_id, "s": session_id, "w": workspace_id, "u": user_id, "t": topic_id, "m": mode, "blueprint": json.dumps(candidates[0], ensure_ascii=False)})
        return self.get_exercise_session(record_id)

    def end_exercise_sessions(self, *, session_id: str, status: str = "cancelled") -> int:
        if status not in {"completed", "cancelled", "expired"}: raise ValueError("invalid exercise session terminal status")
        with self._engine.begin() as c: return c.execute(text("UPDATE nlp_exercise_sessions SET status=:status,completed_at=UTC_TIMESTAMP(6) WHERE conversation_id=:s AND status='active'"), {"s": session_id, "status": status}).rowcount

    def expire_exercise_sessions(self, *, session_id: str, idle_minutes: int = 30) -> int:
        with self._engine.begin() as c: return c.execute(text("UPDATE nlp_exercise_sessions SET status='expired',completed_at=UTC_TIMESTAMP(6) WHERE conversation_id=:s AND status='active' AND updated_at < :cutoff"), {"s": session_id, "cutoff": _now() - timedelta(minutes=max(1, idle_minutes))}).rowcount

    def exercise_questions(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as c: rows = c.execute(text("SELECT * FROM nlp_exercise_questions WHERE exercise_session_id=:id ORDER BY sequence"), {"id": exercise_session_id}).mappings().all()
        return [{**dict(row), "rubric": self._json(row["rubric_json"])} for row in rows]

    def exercise_attempts(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as c: rows = c.execute(text("SELECT a.* FROM nlp_exercise_attempts a JOIN nlp_exercise_questions q ON q.id=a.exercise_question_id WHERE q.exercise_session_id=:id ORDER BY q.sequence,a.attempt_number"), {"id": exercise_session_id}).mappings().all()
        return [{**dict(row), "rubric_matches": self._json(row["rubric_matches_json"]), "passed": bool(row["passed"])} for row in rows]

    def learning_evidence(self, exercise_session_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as c: rows = c.execute(text("SELECT * FROM nlp_learning_evidence WHERE exercise_session_id=:id ORDER BY completed_at,id"), {"id": exercise_session_id}).mappings().all()
        return [{**dict(row), "blueprint_snapshot": self._json(row["blueprint_snapshot_json"]), "knowledge_point_ids": self._json(row["blueprint_snapshot_json"]).get("knowledge_point_ids", [])} for row in rows]

    def exercise_state(self, exercise_session_id: str) -> ExerciseState:
        session = self.get_exercise_session(exercise_session_id)
        if session is None: return ExerciseState()
        questions = self.exercise_questions(exercise_session_id); blueprint = session["blueprint_snapshot"]
        current = next((item for item in reversed(questions) if item["status"] == "awaiting_answer"), None)
        rubric = [str(point.get("criterion", point)) if isinstance(point, dict) else str(point) for point in blueprint.get("rubric", [])]
        attempts = self.exercise_attempts(exercise_session_id)
        if session["status"] != "active": return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id, rubric=rubric, question_number=len(questions), question_count=1, attempt=len(attempts), status="completed")
        if current is None: return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id, rubric=rubric, question_number=len(questions)+1, question_count=1, status="idle")
        return ExerciseState(blueprint_id=blueprint.get("id"), exercise_session_id=exercise_session_id, question=current["question"], rubric=rubric, question_number=int(current["sequence"]), question_count=1, attempt=sum(item["exercise_question_id"] == current["id"] for item in attempts), status="awaiting_answer")

    def record_exercise_question(self, exercise_session_id: str, question: str):
        question = question.strip()
        if not question: raise ValueError("exercise question must not be empty")
        session = self.get_exercise_session(exercise_session_id)
        if session is None or session["status"] != "active": raise ValueError("exercise session is not active")
        questions = self.exercise_questions(exercise_session_id)
        if any(item["status"] == "awaiting_answer" for item in questions): raise ValueError("exercise already has an unanswered question")
        if len(questions) >= 1: raise ValueError("exercise question count is complete")
        record = {"id": str(uuid.uuid4()), "exercise_session_id": exercise_session_id, "sequence": 1, "question": question, "rubric": session["blueprint_snapshot"].get("rubric", []), "status": "awaiting_answer"}
        with self._engine.begin() as c:
            c.execute(text("INSERT INTO nlp_exercise_questions(id,exercise_session_id,sequence,question,rubric_json,status) VALUES(:id,:session,:sequence,:question,:rubric,:status)"), {"id": record["id"], "session": exercise_session_id, "sequence": 1, "question": question, "rubric": json.dumps(record["rubric"], ensure_ascii=False), "status": "awaiting_answer"})
        return record

    def grade_exercise_answer(self, exercise_session_id: str, *, answer: str, matches: list[dict[str, Any]], feedback: str) -> ExerciseState:
        session = self.get_exercise_session(exercise_session_id)
        if session is None or session["status"] != "active": raise ValueError("exercise session is not active")
        question = next((item for item in reversed(self.exercise_questions(exercise_session_id)) if item["status"] == "awaiting_answer"), None)
        if question is None: raise ValueError("exercise has no unanswered question")
        rubric = question["rubric"]
        if {item.get("criterion_index") for item in matches if isinstance(item, dict)} != set(range(len(rubric))) or len(matches) != len(rubric): raise ValueError("grading result does not match blueprint rubric")
        weights = [max(0.0, float(point.get("weight", 1) if isinstance(point, dict) else 1)) for point in rubric]
        score = round(sum(weights[int(item["criterion_index"])] for item in matches if item.get("achieved")) / (sum(weights) or float(len(weights) or 1)) * 100)
        normalized = [{"criterion_index": int(item["criterion_index"]), "criterion": str(rubric[int(item["criterion_index"])].get("criterion", "") if isinstance(rubric[int(item["criterion_index"])], dict) else rubric[int(item["criterion_index"])]), "weight": weights[int(item["criterion_index"])], "achieved": bool(item.get("achieved")), "evidence": str(item.get("evidence", ""))[:1000]} for item in sorted(matches, key=lambda item: int(item["criterion_index"]))]
        attempt_number = sum(item["exercise_question_id"] == question["id"] for item in self.exercise_attempts(exercise_session_id)) + 1
        with self._engine.begin() as c:
            c.execute(text("INSERT INTO nlp_exercise_attempts(id,exercise_question_id,answer,attempt_number,rubric_matches_json,normalized_score,passed,feedback) VALUES(UUID(),:question,:answer,:attempt,:matches,:score,:passed,:feedback)"), {"question": question["id"], "answer": answer, "attempt": attempt_number, "matches": json.dumps(normalized, ensure_ascii=False), "score": score, "passed": score >= 60, "feedback": feedback[:4000]})
            c.execute(text("UPDATE nlp_exercise_questions SET status='completed' WHERE id=:id"), {"id": question["id"]})
            c.execute(text("INSERT INTO nlp_learning_evidence(id,exercise_session_id,exercise_question_id,blueprint_snapshot_json,learner_answer,normalized_score,passed,completed_at) VALUES(UUID(),:session,:question,:blueprint,:answer,:score,:passed,UTC_TIMESTAMP(6))"), {"session": exercise_session_id, "question": question["id"], "blueprint": json.dumps(session["blueprint_snapshot"], ensure_ascii=False), "answer": answer, "score": score, "passed": score >= 60})
            c.execute(text("UPDATE nlp_exercise_sessions SET status='completed',completed_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": exercise_session_id})
        return self.exercise_state(exercise_session_id)

    def get_user_settings(self, user_id: str):
        with self._engine.connect() as c: row = c.execute(text("SELECT revision,preferences_json,updated_at FROM nlp_user_preferences WHERE user_id=:id"), {"id": user_id}).mappings().first()
        return {"revision": int(row["revision"]), "settings": self._json(row["preferences_json"]), "updated_at": row["updated_at"]} if row else {"revision": 0, "settings": {}, "updated_at": None}

    def update_user_settings(self, user_id: str, changes: dict[str, Any]):
        current = self.get_user_settings(user_id); settings = {**current["settings"], **changes}; revision = current["revision"] + 1
        with self._engine.begin() as c: c.execute(text("INSERT INTO nlp_user_preferences(user_id,preferences_json,revision) VALUES(:id,:settings,:revision) ON DUPLICATE KEY UPDATE preferences_json=VALUES(preferences_json),revision=VALUES(revision)"), {"id": user_id, "settings": json.dumps(settings, ensure_ascii=False), "revision": revision})
        return self.get_user_settings(user_id)
    def delete_session(self, session_id: str) -> None:
        with self._engine.begin() as c:
            c.execute(text("DELETE FROM nlp_turn_events WHERE turn_id IN (SELECT id FROM nlp_turns WHERE conversation_id=:s)"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_turn_cancellations WHERE turn_id IN (SELECT id FROM nlp_turns WHERE conversation_id=:s)"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_tool_audits WHERE turn_id IN (SELECT id FROM nlp_turns WHERE conversation_id=:s)"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_tool_calls WHERE turn_id IN (SELECT id FROM nlp_turns WHERE conversation_id=:s)"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_conversation_transcripts WHERE session_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_agent_checkpoints WHERE session_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_memory_archives WHERE session_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_langgraph_checkpoints WHERE thread_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_langgraph_checkpoint_blobs WHERE thread_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_langgraph_checkpoint_writes WHERE thread_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_observability_records WHERE session_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_turns WHERE conversation_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_conversation_messages WHERE conversation_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_guided_sessions WHERE conversation_id=:s"), {"s": session_id})
            c.execute(text("DELETE FROM nlp_exercise_sessions WHERE conversation_id=:s"), {"s": session_id})
            c.execute(text("UPDATE nlp_conversations SET status='deleted', updated_at=UTC_TIMESTAMP(6) WHERE id=:s"), {"s": session_id})
    def latest_event_sequence(self, turn_id: str) -> int:
        with self._engine.connect() as c: return int(c.execute(text("SELECT COALESCE(MAX(sequence),0) FROM nlp_turn_events WHERE turn_id=:id"), {"id": turn_id}).scalar_one())
    def recover_interrupted(self):
        with self._engine.begin() as c:
            rows = c.execute(text("SELECT id FROM nlp_turns WHERE status IN ('accepted','running')")).scalars().all()
            for turn_id in rows:
                c.execute(text("UPDATE nlp_turns SET status='interrupted',error_kind='gateway_restart',error_message='Gateway restarted before the turn completed.',completed_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": turn_id})
        return [self.get_turn(turn_id) for turn_id in rows if self.get_turn(turn_id) is not None]
    def prune_events(self, *, retention_days: int, max_events_per_session: int, now: datetime | None = None):
        cutoff = (now or _now()) - timedelta(days=max(1, retention_days))
        with self._engine.begin() as c:
            compacted = c.execute(text("DELETE e FROM nlp_turn_events e JOIN nlp_turns t ON t.id=e.turn_id WHERE e.created_at < :cutoff AND t.status IN ('completed','failed','cancelled','interrupted') AND e.event_type NOT IN ('message.completed','turn.completed','turn.failed','turn.cancelled')"), {"cutoff": cutoff}).rowcount
            remaining = int(c.execute(text("SELECT COUNT(*) FROM nlp_turn_events")).scalar_one())
        return {"compacted": int(compacted or 0), "capped": 0, "remaining": remaining}
    def flush(self) -> None: return None
    def health(self):
        with self._engine.connect() as c: count = int(c.execute(text("SELECT COUNT(*) FROM nlp_turn_events")).scalar_one())
        return {"database": "mysql", "durable_events": count}

    # Normalized teaching read/write path. The compat projection is deliberately
    # not consulted: catalog revision and entity rows are authoritative here.
    def _ensure_workspace(self, connection: Any, workspace_id: str) -> None:
        connection.execute(
            text(
                "INSERT IGNORE INTO nlp_workspaces(id,slug,name,status) "
                "VALUES(:id,:slug,:name,'active')"
            ),
            {"id": workspace_id, "slug": workspace_id[:64], "name": workspace_id[:128]},
        )

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value) if value else {}

    def get_teaching_catalog(self, workspace_id: str):
        with self._engine.connect() as connection:
            catalog = connection.execute(
                text("SELECT revision,updated_at FROM nlp_course_catalogs WHERE workspace_id=:workspace_id"),
                {"workspace_id": workspace_id},
            ).mappings().first()
            if catalog is None:
                return {
                    "workspace_id": workspace_id,
                    "revision": 0,
                    "updated_at": None,
                    "catalog": {
                        "workspace_id": workspace_id,
                        "topics": [],
                        "exercise_blueprints": [],
                        "review_blueprints": [],
                        "guided_blueprints": [],
                    },
                }
            topic_rows = connection.execute(
                text("SELECT id,name,description,status,sort_order FROM nlp_course_topics WHERE workspace_id=:workspace_id ORDER BY sort_order,id"),
                {"workspace_id": workspace_id},
            ).mappings().all()
            point_rows = connection.execute(
                text("SELECT id,topic_id,name,markdown,status,sort_order FROM nlp_knowledge_points WHERE workspace_id=:workspace_id ORDER BY sort_order,id"),
                {"workspace_id": workspace_id},
            ).mappings().all()
            points_by_topic: dict[str, list[dict[str, Any]]] = {}
            for point in point_rows:
                points_by_topic.setdefault(str(point["topic_id"]), []).append(
                    {
                        "id": point["id"], "name": point["name"], "markdown": point["markdown"],
                        "status": point["status"], "sort_order": point["sort_order"],
                    }
                )
            topics = [
                {
                    "id": row["id"], "name": row["name"], "description": row["description"],
                    # ``sort_order`` is persisted for deterministic storage
                    # ordering, but it is not part of the public CourseTopic
                    # contract.  Keep the transport shape aligned with the
                    # SQLite repository and the strict teacher schemas.
                    "status": row["status"],
                    "knowledge_points": points_by_topic.get(str(row["id"]), []),
                }
                for row in topic_rows
            ]
            blueprint_rows = connection.execute(
                text("SELECT kind,payload_json FROM nlp_teaching_blueprints WHERE workspace_id=:workspace_id ORDER BY id"),
                {"workspace_id": workspace_id},
            ).mappings().all()
        blueprints = {"exercise": [], "review": [], "guided": []}
        for row in blueprint_rows:
            blueprint = self._json(row["payload_json"])
            # ``kind`` is the normalized table discriminator, not a public
            # blueprint field.  Older payloads may not contain it, while newer
            # writes store it for persistence queries; strip both forms at the
            # repository boundary before Pydantic validation.
            blueprint.pop("kind", None)
            blueprints.get(str(row["kind"]), []).append(blueprint)
        value = {
            "workspace_id": workspace_id,
            "topics": topics,
            "exercise_blueprints": blueprints["exercise"],
            "review_blueprints": blueprints["review"],
            "guided_blueprints": blueprints["guided"],
        }
        return {"workspace_id": workspace_id, "revision": int(catalog["revision"]), "updated_at": catalog["updated_at"], "catalog": value}

    def update_teaching_catalog(self, workspace_id: str, catalog: dict[str, Any]):
        groups = {
            "exercise": list(catalog.get("exercise_blueprints", [])),
            "review": list(catalog.get("review_blueprints", [])),
            "guided": list(catalog.get("guided_blueprints", [])),
        }
        with self._engine.begin() as connection:
            self._ensure_workspace(connection, workspace_id)
            row = connection.execute(
                text("SELECT revision FROM nlp_course_catalogs WHERE workspace_id=:workspace_id FOR UPDATE"),
                {"workspace_id": workspace_id},
            ).mappings().first()
            revision = int(row["revision"]) + 1 if row else 1
            if row is None:
                connection.execute(
                    text("INSERT INTO nlp_course_catalogs(workspace_id,revision) VALUES(:workspace_id,:revision)"),
                    {"workspace_id": workspace_id, "revision": revision},
                )
            else:
                connection.execute(
                    text("UPDATE nlp_course_catalogs SET revision=:revision WHERE workspace_id=:workspace_id"),
                    {"workspace_id": workspace_id, "revision": revision},
                )
            connection.execute(text("DELETE FROM nlp_teaching_blueprints WHERE workspace_id=:workspace_id"), {"workspace_id": workspace_id})
            connection.execute(text("DELETE FROM nlp_course_topics WHERE workspace_id=:workspace_id"), {"workspace_id": workspace_id})
            for topic_order, topic in enumerate(catalog.get("topics", [])):
                topic_id = str(topic["id"])
                connection.execute(
                    text("INSERT INTO nlp_course_topics(id,workspace_id,name,description,status,sort_order) VALUES(:id,:workspace_id,:name,:description,:status,:sort_order)"),
                    {"id": topic_id, "workspace_id": workspace_id, "name": str(topic.get("name", "")), "description": str(topic.get("description", "")), "status": str(topic.get("status", "enabled")), "sort_order": int(topic.get("sort_order", topic_order))},
                )
                for point_order, point in enumerate(topic.get("knowledge_points", [])):
                    connection.execute(
                        text("INSERT INTO nlp_knowledge_points(id,workspace_id,topic_id,name,markdown,status,sort_order) VALUES(:id,:workspace_id,:topic_id,:name,:markdown,:status,:sort_order)"),
                        {"id": str(point["id"]), "workspace_id": workspace_id, "topic_id": topic_id, "name": str(point.get("name", "")), "markdown": str(point.get("markdown", "")), "status": str(point.get("status", "enabled")), "sort_order": int(point.get("sort_order", point_order))},
                    )
            for kind, blueprints in groups.items():
                for blueprint in blueprints:
                    connection.execute(
                        text("INSERT INTO nlp_teaching_blueprints(id,workspace_id,kind,topic_id,knowledge_point_id,status,payload_json,revision) VALUES(:id,:workspace_id,:kind,:topic_id,:knowledge_point_id,:status,:payload_json,:revision)"),
                        {"id": str(blueprint["id"]), "workspace_id": workspace_id, "kind": kind, "topic_id": str(blueprint["topic_id"]), "knowledge_point_id": blueprint.get("knowledge_point_id"), "status": str(blueprint.get("status", "draft")), "payload_json": json.dumps({**blueprint, "kind": kind}, ensure_ascii=False), "revision": int(blueprint.get("revision", 0))},
                    )
        result = self.get_teaching_catalog(workspace_id)
        result["revision"] = revision
        return result

    def get_knowledge_page(self, workspace_id: str, knowledge_point_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,"
                    "revision,published_revision,updated_at FROM nlp_knowledge_pages "
                    "WHERE workspace_id=:workspace_id AND knowledge_point_id=:knowledge_point_id"
                ),
                {"workspace_id": workspace_id, "knowledge_point_id": knowledge_point_id},
            ).mappings().first()
        return dict(row) if row else None

    def list_knowledge_pages(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,"
                    "revision,published_revision,updated_at FROM nlp_knowledge_pages "
                    "WHERE workspace_id=:workspace_id"
                ),
                {"workspace_id": workspace_id},
            ).mappings().all()
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
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,"
                    "revision,published_revision,updated_at FROM nlp_knowledge_pages "
                    "WHERE workspace_id=:workspace_id AND knowledge_point_id=:knowledge_point_id FOR UPDATE"
                ),
                {"workspace_id": workspace_id, "knowledge_point_id": knowledge_point_id},
            ).mappings().first()
            current = dict(row) if row else None
            revision = self._check_knowledge_page_revision(current, expected_revision) + 1
            if current is None:
                connection.execute(
                    text(
                        "INSERT INTO nlp_knowledge_pages("
                        "id,workspace_id,knowledge_point_id,draft_markdown,revision) "
                        "VALUES(:id,:workspace_id,:knowledge_point_id,:draft_markdown,:revision)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "workspace_id": workspace_id,
                        "knowledge_point_id": knowledge_point_id,
                        "draft_markdown": draft_markdown,
                        "revision": revision,
                    },
                )
            else:
                connection.execute(
                    text(
                        "UPDATE nlp_knowledge_pages SET draft_markdown=:draft_markdown,revision=:revision,"
                        "updated_at=UTC_TIMESTAMP(6) "
                        "WHERE workspace_id=:workspace_id AND knowledge_point_id=:knowledge_point_id"
                    ),
                    {
                        "draft_markdown": draft_markdown,
                        "revision": revision,
                        "workspace_id": workspace_id,
                        "knowledge_point_id": knowledge_point_id,
                    },
                )
        return self.get_knowledge_page(workspace_id, knowledge_point_id)  # type: ignore[return-value]

    def publish_knowledge_page(
        self,
        workspace_id: str,
        knowledge_point_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT draft_markdown,revision FROM nlp_knowledge_pages "
                    "WHERE workspace_id=:workspace_id AND knowledge_point_id=:knowledge_point_id FOR UPDATE"
                ),
                {"workspace_id": workspace_id, "knowledge_point_id": knowledge_point_id},
            ).mappings().first()
            current = dict(row) if row else None
            if current is None:
                raise ValueError("教材页面尚未保存草稿")
            revision = self._check_knowledge_page_revision(current, expected_revision)
            if not str(current["draft_markdown"]).strip():
                raise ValueError("教材正文为空，不能发布")
            connection.execute(
                text(
                    "UPDATE nlp_knowledge_pages SET published_markdown=draft_markdown,"
                    "published_revision=:revision,updated_at=UTC_TIMESTAMP(6) "
                    "WHERE workspace_id=:workspace_id "
                    "AND knowledge_point_id=:knowledge_point_id"
                ),
                {
                    "revision": revision,
                    "workspace_id": workspace_id,
                    "knowledge_point_id": knowledge_point_id,
                },
            )
            for asset_path in self._asset_paths_from_markdown(str(current["draft_markdown"])):
                connection.execute(
                    text(
                        "UPDATE nlp_knowledge_book_assets SET published_content=draft_content,"
                        "updated_at=UTC_TIMESTAMP(6) WHERE workspace_id=:workspace_id AND asset_path=:asset_path"
                    ),
                    {"workspace_id": workspace_id, "asset_path": asset_path},
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
        with self._engine.begin() as connection:
            currents: dict[str, dict[str, Any] | None] = {}
            for page in pages:
                point_id = str(page["knowledge_point_id"])
                row = connection.execute(
                    text(
                        "SELECT workspace_id,knowledge_point_id,draft_markdown,published_markdown,"
                        "revision,published_revision,updated_at FROM nlp_knowledge_pages "
                        "WHERE workspace_id=:workspace_id AND knowledge_point_id=:knowledge_point_id FOR UPDATE"
                    ),
                    {"workspace_id": workspace_id, "knowledge_point_id": point_id},
                ).mappings().first()
                current = dict(row) if row else None
                self._check_knowledge_page_revision(current, int(page["expected_revision"]))
                currents[point_id] = current

            for page in pages:
                point_id = str(page["knowledge_point_id"])
                current = currents[point_id]
                revision = int(page["expected_revision"]) + 1
                if current is None:
                    connection.execute(
                        text(
                            "INSERT INTO nlp_knowledge_pages(id,workspace_id,knowledge_point_id,draft_markdown,revision) "
                            "VALUES(:id,:workspace_id,:knowledge_point_id,:draft_markdown,:revision)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "workspace_id": workspace_id,
                            "knowledge_point_id": point_id,
                            "draft_markdown": str(page["content_markdown"]),
                            "revision": revision,
                        },
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE nlp_knowledge_pages SET draft_markdown=:draft_markdown,revision=:revision,"
                            "updated_at=UTC_TIMESTAMP(6) WHERE workspace_id=:workspace_id "
                            "AND knowledge_point_id=:knowledge_point_id"
                        ),
                        {
                            "draft_markdown": str(page["content_markdown"]),
                            "revision": revision,
                            "workspace_id": workspace_id,
                            "knowledge_point_id": point_id,
                        },
                    )

            for asset in assets:
                content = bytes(asset["content"])
                connection.execute(
                    text(
                        "INSERT INTO nlp_knowledge_book_assets(workspace_id,asset_path,media_type,draft_content,"
                        "size_bytes,sha256) VALUES(:workspace_id,:asset_path,:media_type,:draft_content,:size_bytes,:sha256) "
                        "ON DUPLICATE KEY UPDATE media_type=VALUES(media_type),draft_content=VALUES(draft_content),"
                        "size_bytes=VALUES(size_bytes),sha256=VALUES(sha256),updated_at=UTC_TIMESTAMP(6)"
                    ),
                    {
                        "workspace_id": workspace_id,
                        "asset_path": str(asset["asset_path"]),
                        "media_type": str(asset["media_type"]),
                        "draft_content": content,
                        "size_bytes": len(content),
                        "sha256": str(asset.get("sha256") or hashlib.sha256(content).hexdigest()),
                    },
                )
        return [self.get_knowledge_page(workspace_id, str(page["knowledge_point_id"])) for page in pages]  # type: ignore[list-item]

    def get_knowledge_book_asset(self, workspace_id: str, asset_path: str) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT workspace_id,asset_path,media_type,published_content AS content,size_bytes,sha256 "
                    "FROM nlp_knowledge_book_assets WHERE workspace_id=:workspace_id AND asset_path=:asset_path"
                ),
                {"workspace_id": workspace_id, "asset_path": asset_path},
            ).mappings().first()
        if row is None or row["content"] is None:
            return None
        return dict(row)

    def teaching_topic(self, workspace_id: str, topic_id: str):
        catalog = self.get_teaching_catalog(workspace_id)["catalog"]
        topic = next((item for item in catalog["topics"] if item["id"] == topic_id and item.get("status", "enabled") == "enabled"), None)
        if topic is None:
            return None
        points = [item for item in topic["knowledge_points"] if item.get("status", "enabled") == "enabled"]
        if sum(len(str(item.get("markdown") or item.get("name") or "")) for item in points) > self.knowledge_point_prompt_budget:
            raise TeachingConfigurationError(f"主题“{topic_id}”的知识点内容超过提示词预算")
        return {"id": topic_id, "name": topic.get("name", topic_id), "description": topic.get("description", ""), "knowledge_points": points}

    def select_guided_blueprint(self, *, workspace_id: str, topic_id: str):
        return next((item for item in self.get_teaching_catalog(workspace_id)["catalog"]["guided_blueprints"] if item.get("topic_id") == topic_id and item.get("status", "enabled") == "enabled"), None)

    def close(self) -> None: self._engine.dispose()
