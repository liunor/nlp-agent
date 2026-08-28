import json
from datetime import datetime, timedelta, timezone

import pytest

from core.learning import ExerciseState, LearningContext, LearningProgress
from gateway.contracts import GatewayEventType, TurnStatus
from gateway.contracts import TeachingConfigurationError
from gateway.repository import GatewayRepository


def test_gateway_repository_idempotency_event_order_and_recovery(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    turn, duplicate = repository.create_turn(
        turn_id="turn-1",
        session_id="session-1",
        workspace_id="workspace-1",
        user_id="alice",
        input_text="hello",
        idempotency_key="idem-1",
    )
    assert duplicate is False
    same, duplicate = repository.create_turn(
        turn_id="turn-other",
        session_id="session-1",
        workspace_id="workspace-1",
        user_id="alice",
        input_text="hello again",
        idempotency_key="idem-1",
    )
    assert duplicate is True
    assert same.turn_id == turn.turn_id

    repository.update_turn(turn.turn_id, TurnStatus.RUNNING)
    first = repository.append_event(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        event_type=GatewayEventType.TURN_STARTED,
    )
    second = repository.append_event(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        event_type=GatewayEventType.MESSAGE_DELTA,
        payload={"delta": "hi"},
    )
    assert [event.sequence for event in repository.events_after(turn.turn_id)] == [1, 2]
    assert repository.health()["durable_events"] == 2
    assert repository._conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='gateway_outbox'"
    ).fetchone()[0] == 0

    recovered = repository.recover_interrupted()
    assert recovered[0].status == TurnStatus.INTERRUPTED
    repository.close()


def test_ensure_event_repairs_terminal_log_once(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    turn, _ = repository.create_turn(
        turn_id="turn-1",
        session_id="session-1",
        workspace_id="workspace-1",
        user_id="alice",
        input_text="hello",
        idempotency_key=None,
    )
    repository.update_turn(turn.turn_id, TurnStatus.COMPLETED, final_text="answer")

    first = repository.ensure_event(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        event_type=GatewayEventType.TURN_COMPLETED,
        payload={"content": "answer"},
    )
    second = repository.ensure_event(
        turn_id=turn.turn_id,
        session_id=turn.session_id,
        event_type=GatewayEventType.TURN_COMPLETED,
        payload={"content": "answer"},
    )

    assert second.event_id == first.event_id
    assert len(repository.events_after(turn.turn_id)) == 1
    repository.close()


def test_event_retention_compacts_terminal_turns_caps_sessions_and_keeps_active(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    terminal_ids = []
    for index in range(2):
        turn, _ = repository.create_turn(
            turn_id=f"terminal-{index}",
            session_id="session-1",
            workspace_id="workspace-1",
            user_id="alice",
            input_text="done",
            idempotency_key=None,
        )
        terminal_ids.append(turn.turn_id)
        repository.update_turn(turn.turn_id, TurnStatus.COMPLETED, final_text="answer")
        for event_type in (
            GatewayEventType.TURN_ACCEPTED,
            GatewayEventType.MESSAGE_DELTA,
            GatewayEventType.MESSAGE_COMPLETED,
            GatewayEventType.TURN_COMPLETED,
        ):
            repository.append_event(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                event_type=event_type,
            )

    active, _ = repository.create_turn(
        turn_id="active",
        session_id="session-1",
        workspace_id="workspace-1",
        user_id="alice",
        input_text="running",
        idempotency_key=None,
    )
    repository.update_turn(active.turn_id, TurnStatus.RUNNING)
    for event_type in (GatewayEventType.TURN_STARTED, GatewayEventType.MESSAGE_DELTA):
        repository.append_event(
            turn_id=active.turn_id,
            session_id=active.session_id,
            event_type=event_type,
        )

    stats = repository.prune_events(
        retention_days=7,
        max_events_per_session=3,
        now=datetime.now(timezone.utc) + timedelta(days=8),
    )

    assert stats == {"compacted": 4, "capped": 1, "remaining": 5}
    assert [event.type for event in repository.events_after(active.turn_id)] == [
        GatewayEventType.TURN_STARTED,
        GatewayEventType.MESSAGE_DELTA,
    ]
    terminal_events = sum(
        len(repository.events_after(turn_id)) for turn_id in terminal_ids
    )
    assert terminal_events == 3
    repository.close()


def test_learning_state_and_teaching_catalog_are_isolated_from_turn_history(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    context = LearningContext(topic="Transformer", level="intermediate", mode="practice")
    progress = LearningProgress(objective="理解注意力机制", stage="practice")
    exercise = ExerciseState(question="解释 Q、K、V 的作用", rubric=["说明三个向量"], status="awaiting_answer")
    turn, _ = repository.create_turn(
        turn_id="turn-learning", session_id="session-learning", workspace_id="workspace-1",
        user_id="alice", input_text="开始练习", idempotency_key=None,
        learning_context=context, learning_progress=progress, exercise_state=exercise,
    )
    restored = repository.get_turn(turn.turn_id)
    assert restored is not None
    assert restored.learning_context == context
    assert restored.learning_progress == progress
    assert restored.exercise_state == exercise

    saved_catalog = repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{"id": "transformer", "name": "Transformer", "description": "", "knowledge_points": []}],
        "exercise_blueprints": [], "review_blueprints": [],
    })
    assert saved_catalog["revision"] == 1
    assert repository.get_turn(turn.turn_id).input_text == "开始练习"
    assert repository.list_turns("session-learning")[0].turn_id == "turn-learning"
    assert repository.get_teaching_catalog("workspace-1")["catalog"]["topics"][0]["name"] == "Transformer"
    repository.close()


def test_knowledge_book_page_keeps_draft_and_published_content_separate(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{
            "id": "transformer", "name": "Transformer", "description": "",
            "knowledge_points": [{"id": "attention", "name": "注意力", "markdown": "Q、K、V", "status": "enabled"}],
        }],
        "exercise_blueprints": [], "review_blueprints": [], "guided_blueprints": [],
    })

    saved = repository.update_knowledge_page(
        "workspace-1", "attention", "# 注意力\n\n草稿内容", expected_revision=0,
    )
    assert saved["revision"] == 1
    assert saved["published_markdown"] is None
    assert repository.get_published_knowledge_page("workspace-1", "attention") is None

    published = repository.publish_knowledge_page("workspace-1", "attention", expected_revision=1)
    assert published["published_revision"] == 1
    assert published["published_markdown"] == "# 注意力\n\n草稿内容"

    repository.update_knowledge_page(
        "workspace-1", "attention", "# 注意力\n\n第二版", expected_revision=1,
    )
    assert repository.get_published_knowledge_page("workspace-1", "attention")["published_markdown"] == "# 注意力\n\n草稿内容"
    repository.close()


def test_turn_persists_the_real_guided_session_and_blueprint_snapshot_reference(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    turn, _ = repository.create_turn(
        turn_id="guided-turn", session_id="chat-1", workspace_id="workspace-1",
        user_id="alice", input_text="带我理解 TF-IDF", idempotency_key=None,
        guided_session_id="guided-1", guided_blueprint_id="tfidf-guide",
        guided_blueprint_snapshot_sha256="a" * 64,
    )

    restored = repository.get_turn(turn.turn_id)

    assert restored is not None
    assert restored.guided_session is not None
    assert restored.guided_session.id == "guided-1"
    assert restored.guided_session.blueprint_id == "tfidf-guide"
    assert restored.guided_session.blueprint_snapshot_sha256 == "a" * 64
    repository.close()


def test_clear_learning_sessions_keeps_teacher_catalog_and_user_settings(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("workspace-1", {"workspace_id": "workspace-1", "topics": [{"id": "attention"}], "exercise_blueprints": [], "review_blueprints": []})
    turn, _ = repository.create_turn(turn_id="turn-reset", session_id="session-reset", workspace_id="workspace-1", user_id="alice", input_text="reset", idempotency_key=None)
    repository.append_event(turn_id=turn.turn_id, session_id=turn.session_id, event_type=GatewayEventType.TURN_STARTED)
    repository._conn.execute(
        """INSERT INTO gateway_exercise_sessions(
           id,session_id,workspace_id,user_id,topic_id,mode,status,blueprint_snapshot_json,created_at,updated_at,completed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("exercise-reset", "session-reset", "workspace-1", "alice", "attention", "practice", "active", "{}", "now", "now", None),
    )

    assert repository.clear_learning_sessions() == {
        "gateway_turns": 1, "gateway_events": 1, "gateway_exercise_sessions": 1,
        "gateway_exercise_questions": 0, "gateway_exercise_attempts": 0, "gateway_learning_evidence": 0,
    }
    assert repository.list_turns("session-reset") == []
    assert repository.get_teaching_catalog("workspace-1")["catalog"]["topics"] == [{"id": "attention"}]
    repository.close()


def test_exercise_session_uses_an_enabled_blueprint_snapshot_and_keeps_it_immutable(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{"id": "transformer", "name": "Transformer", "description": "", "status": "enabled", "knowledge_points": []}],
        "exercise_blueprints": [
            {"id": "draft", "name": "草稿", "topic_id": "transformer", "level": "beginner", "instructions": "不要使用", "question_types": [], "question_count": 1, "status": "draft", "knowledge_point_ids": [], "rubric": []},
            {"id": "enabled", "name": "注意力练习", "topic_id": "transformer", "level": "beginner", "instructions": "解释 QKV", "question_types": ["填空"], "question_count": 1, "status": "enabled", "knowledge_point_ids": [], "rubric": []},
        ],
        "review_blueprints": [],
    })

    session = repository.start_exercise_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice", topic_id="transformer", mode="practice",
    )

    assert session is not None
    assert session["blueprint_snapshot"]["id"] == "enabled"
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1", "topics": [], "exercise_blueprints": [], "review_blueprints": [],
    })
    assert repository.get_exercise_session(session["id"])["blueprint_snapshot"]["instructions"] == "解释 QKV"
    assert repository.start_exercise_session(
        session_id="chat-2", workspace_id="workspace-1", user_id="alice", topic_id="transformer", mode="practice",
    ) is None
    repository.close()


def test_teaching_topic_rejects_knowledge_points_over_the_configured_prompt_budget(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3", knowledge_point_prompt_budget=10)
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{
            "id": "attention", "name": "Attention", "description": "", "status": "enabled",
            "knowledge_points": [
                {"id": "one", "name": "one", "markdown": "12345", "status": "enabled", "sort_order": 1},
                {"id": "two", "name": "two", "markdown": "678901", "status": "enabled", "sort_order": 2},
            ],
        }],
        "exercise_blueprints": [], "review_blueprints": [],
    })

    with pytest.raises(TeachingConfigurationError, match="知识点内容超过提示词预算"):
        repository.teaching_topic("workspace-1", "attention")

    repository.close()


def test_guided_session_expires_after_its_idle_window(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    guided = repository.start_or_resume_guided_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice",
        topic_id="attention", first_message="带我理解注意力机制",
    )
    repository._conn.execute(
        "UPDATE gateway_guided_sessions SET updated_at=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(), guided["id"]),
    )

    assert repository.expire_guided_sessions(session_id="chat-1") == 1
    assert repository.active_guided_session(session_id="chat-1", topic_id="attention") is None
    repository.close()


def test_guided_session_can_persist_learning_summary_and_complete(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    guided = repository.start_or_resume_guided_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice",
        topic_id="attention", first_message="带我理解注意力机制",
    )

    repository.advance_guided_session(
        guided["id"], tutor_message="你已经掌握了 QKV。", completed=True,
        known_concepts=["Q、K、V 的角色"], misconceptions=["softmax 不是可省略步骤"],
    )

    assert repository.active_guided_session(session_id="chat-1", topic_id="attention") is None
    row = repository._conn.execute("SELECT * FROM gateway_guided_sessions WHERE id=?", (guided["id"],)).fetchone()
    assert row["status"] == "completed"
    assert json.loads(row["known_concepts_json"]) == ["Q、K、V 的角色"]
    assert json.loads(row["misconceptions_json"]) == ["softmax 不是可省略步骤"]
    repository.close()


def test_guided_session_keeps_its_assigned_blueprint_snapshot(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    blueprint = {"id": "guided-qkv", "name": "QKV 引导", "topic_id": "attention", "knowledge_point_id": "qkv", "guidance": "先比较 Q 与 K。", "status": "enabled"}
    guided = repository.start_or_resume_guided_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice", topic_id="attention",
        first_message="带我理解注意力机制", guided_blueprint=blueprint,
    )
    assert guided["guided_blueprint"] == blueprint

    resumed = repository.start_or_resume_guided_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice", topic_id="attention",
        first_message="我先回答一下", guided_blueprint={"id": "new", "guidance": "不应替换"},
    )
    assert resumed["guided_blueprint"] == blueprint
    repository.close()


def test_exercise_session_expires_and_rejects_incomplete_rubric_grading(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{"id": "attention", "name": "Attention", "status": "enabled", "knowledge_points": [{"id": "qkv", "name": "QKV", "markdown": "QKV", "status": "enabled", "sort_order": 0}]}],
        "exercise_blueprints": [{
            "id": "bp", "name": "练习", "topic_id": "attention", "level": "beginner", "status": "enabled",
            "instructions": "", "question_types": ["简答"], "question_count": 1, "knowledge_point_ids": [],
            "rubric": [{"criterion": "说明用途", "weight": 1}, {"criterion": "准确", "weight": 1}],
        }], "review_blueprints": [],
    })
    exercise = repository.start_exercise_session(
        session_id="chat-1", workspace_id="workspace-1", user_id="alice", topic_id="attention", mode="practice"
    )
    assert exercise is not None
    repository.record_exercise_question(exercise["id"], "解释 attention")

    with pytest.raises(ValueError, match="does not match blueprint rubric"):
        repository.grade_exercise_answer(
            exercise["id"], answer="我的回答", matches=[{"criterion_index": 0, "achieved": True}],
            feedback="",
        )
    assert repository.exercise_attempts(exercise["id"]) == []

    repository._conn.execute(
        "UPDATE gateway_exercise_sessions SET updated_at=? WHERE id=?",
        ((datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(), exercise["id"]),
    )
    assert repository.expire_exercise_sessions(session_id="chat-1") == 1
    assert repository.get_exercise_session(exercise["id"])["status"] == "expired"
    repository.close()


def test_completed_exercise_state_exposes_the_real_attempt_count(tmp_path):
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    repository.update_teaching_catalog("workspace-1", {
        "workspace_id": "workspace-1",
        "topics": [{"id": "attention", "name": "Attention", "status": "enabled", "knowledge_points": [{"id": "qkv", "name": "QKV", "markdown": "QKV", "status": "enabled", "sort_order": 0}]}],
        "exercise_blueprints": [{"id": "enabled", "name": "练习", "topic_id": "attention", "knowledge_point_id": "qkv", "instructions": "解释 QKV", "question_type": "简答", "status": "enabled", "rubric": [{"criterion": "说明 Q"}]}],
        "review_blueprints": [],
    })
    exercise = repository.start_exercise_session(session_id="chat-1", workspace_id="workspace-1", user_id="alice", topic_id="attention", mode="practice")
    assert exercise is not None
    repository.record_exercise_question(exercise["id"], "解释 Q 的作用")
    repository.grade_exercise_answer(exercise["id"], answer="Q 是查询", matches=[{"criterion_index": 0, "achieved": True, "evidence": "Q 是查询"}], feedback="正确")

    state = repository.exercise_state(exercise["id"])

    assert state.status == "completed"
    assert state.attempt == 1
    repository.close()
