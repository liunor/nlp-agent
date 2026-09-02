"""Performance and data-integrity behaviour of teacher analytics data fetch.

Covers the dedup of the evidence/criterion read-model queries, the ``truncated``
flag that surfaces when a LIMIT silently drops the oldest rows, and the
``filter_period_rows`` helper used to slice a broad fetch to the period.
"""

import asyncio
from datetime import date
from types import SimpleNamespace

from gateway.repository import GatewayRepository
from server.teacher.analytics import filter_period_rows
from server.teacher import service as service_module
from server.teacher.service import TeacherService


def _evidence(completed_at, score, passed, *, user="student-1"):
    return {
        "user_id": user,
        "topic_id": "transformer",
        "mode": "practice",
        "knowledge_point_ids": ["attention"],
        "score": score,
        "passed": passed,
        "completed_at": completed_at,
    }


def _catalog():
    return {
        "catalog": {
            "topics": [
                {
                    "id": "transformer",
                    "name": "Transformer 与注意力",
                    "knowledge_points": [{"id": "attention", "name": "自注意力"}],
                }
            ]
        }
    }


class FakeRepository:
    def __init__(self, *, evidence_rows, criterion_rows, truncated=False):
        self._evidence_rows = evidence_rows
        self._criterion_rows = criterion_rows
        self._truncated = truncated
        self.evidence_calls = 0
        self.criterion_calls = 0

    def get_teaching_catalog(self, workspace_id):
        return _catalog()

    def list_student_user_ids(self):
        return {"student-1"}

    def list_question_turns(self, *, workspace_id, since, timezone_name="UTC"):
        return [
            {
                "session_id": "s1",
                "user_id": "student-1",
                "display_name": "张三",
                "username": "zhangsan",
                "role_codes": ["student"],
                "has_error": False,
                "topic_id": "transformer",
                "level": "beginner",
                "mode": "explain",
                "day": "2026-08-10",
                "hour": 9,
                "weekday": 2,
            }
        ]

    def guided_session_stats(self, *, workspace_id, since):
        return []

    def exercise_evidence_stats(self, *, workspace_id, since, until=None):
        self.evidence_calls += 1
        return self._evidence_rows, self._truncated

    def exercise_criterion_stats(self, *, workspace_id, since, until=None):
        self.criterion_calls += 1
        return self._criterion_rows, self._truncated


def _run_analytics(fake_repository, monkeypatch):
    monkeypatch.setattr(
        service_module, "authorization_service", SimpleNamespace(require=lambda *a, **k: None)
    )
    gateway = SimpleNamespace(repository=fake_repository)
    return asyncio.run(
        TeacherService().analytics(
            None,
            gateway,
            "workspace-1",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
    )


def test_analytics_fetches_evidence_and_criterion_only_once(monkeypatch):
    fake = FakeRepository(
        evidence_rows=[
            _evidence("2026-08-10T10:00:00", 90, True),
            _evidence("2026-08-20T10:00:00", 60, False),
            # Outside the period, still in the broad history window.
            _evidence("2026-07-15T10:00:00", 80, True),
        ],
        criterion_rows=[],
    )
    result = _run_analytics(fake, monkeypatch)

    assert fake.evidence_calls == 1
    assert fake.criterion_calls == 1
    # The overview slice keeps only the two in-period rows.
    assert result["summary"]["exercises"] == 2
    assert result["truncated"] is False


def test_analytics_surfaces_truncated_read_model(monkeypatch):
    fake = FakeRepository(
        evidence_rows=[_evidence("2026-08-10T10:00:00", 90, True)],
        criterion_rows=[],
        truncated=True,
    )
    result = _run_analytics(fake, monkeypatch)

    assert result["truncated"] is True


def test_analytics_reports_structured_data_completeness(monkeypatch):
    fake = FakeRepository(
        evidence_rows=[_evidence("2026-08-10T10:00:00", 90, True)],
        criterion_rows=[],
        truncated=True,
    )
    result = _run_analytics(fake, monkeypatch)

    completeness = result["data_completeness"]
    assert completeness["complete"] is False
    assert completeness["evidence_truncated"] is True
    assert completeness["message"]


def test_analytics_reports_complete_data_without_message(monkeypatch):
    fake = FakeRepository(
        evidence_rows=[_evidence("2026-08-10T10:00:00", 90, True)],
        criterion_rows=[],
        truncated=False,
    )
    result = _run_analytics(fake, monkeypatch)

    completeness = result["data_completeness"]
    assert completeness["complete"] is True
    assert completeness["evidence_truncated"] is False
    assert completeness["message"] is None


def test_filter_period_rows_keeps_window_rows_and_drops_others():
    rows = [
        {"completed_at": "2026-08-10T00:00:00"},
        {"completed_at": "2026-07-31T00:00:00"},
        {"completed_at": "2026-08-31T23:59:59"},
        {},  # date-less rows are kept
    ]
    kept = filter_period_rows(rows, date(2026, 8, 1), date(2026, 8, 31))

    assert len(kept) == 3
    assert rows[0] in kept
    assert rows[2] in kept
    assert rows[3] in kept
    assert rows[1] not in kept


def test_exercise_evidence_stats_reports_truncation(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    for index in range(3):
        session_id = f"session-{index}"
        question_id = f"question-{index}"
        evidence_id = f"evidence-{index}"
        completed = f"2026-08-0{index + 1}T10:00:00"
        repository._conn.execute(
            "INSERT INTO gateway_exercise_sessions(id,session_id,workspace_id,user_id,topic_id,mode,status,blueprint_snapshot_json,created_at,updated_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, f"conv-{index}", "workspace-w", "student-1", "transformer", "practice", "completed", "{}", "2026-08-01T00:00:00", "2026-08-01T00:00:00", completed),
        )
        repository._conn.execute(
            "INSERT INTO gateway_exercise_questions(id,exercise_session_id,sequence,question,rubric_json,status,created_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (question_id, session_id, 0, "题目", "[]", "completed", "2026-08-01T00:00:00", completed),
        )
        repository._conn.execute(
            "INSERT INTO gateway_learning_evidence(id,exercise_session_id,exercise_question_id,blueprint_snapshot_json,question,learner_answer,attempt_number,rubric_matches_json,normalized_score,passed,knowledge_point_ids_json,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, session_id, question_id, "{}", "题目", "答", 1, "[]", 80, 1, '["attention"]', completed),
        )
    repository._conn.commit()

    rows, truncated = repository.exercise_evidence_stats(workspace_id="workspace-w", since="2026-08-01", limit=2)
    assert truncated is True
    assert len(rows) == 2
    # Newest first when truncated.
    assert rows[0]["question_id"] == "question-2"

    rows, truncated = repository.exercise_evidence_stats(workspace_id="workspace-w", since="2026-08-01", limit=10)
    assert truncated is False
    assert len(rows) == 3
    repository.close()