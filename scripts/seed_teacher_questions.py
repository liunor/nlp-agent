"""Seed realistic teacher question analytics data into one local MySQL workspace.

This is an explicit local test-data command, not a frontend demo mode.  It
creates only accounts with the effective ``student`` RBAC role and writes
historical conversations/turns directly into the normal read model used by
the teacher overview endpoint.

Usage:
    uv run python -m scripts.seed_teacher_questions --workspace-id <id>
"""

from __future__ import annotations

import argparse
import json
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from math import floor
from typing import Any

from sqlalchemy import create_engine, text

from configs.settings import settings


TARGET_WORKSPACE = "a99241e8-6094-42d8-bf81-911bf1a02c89"
SEED_NAMESPACE = uuid.UUID("a3f0ce9e-32c8-4d65-8d50-7a04f0a7a9e7")
SEED_REGISTRATION_SOURCE = "teacher_question_seed"

STUDENT_NAMES = (
    "林晓晨",
    "周子涵",
    "陈思远",
    "何雨桐",
    "赵一鸣",
    "孙嘉怡",
    "王浩然",
    "刘欣悦",
    "杨博文",
    "吴静怡",
    "郑凯文",
    "徐梦瑶",
)

TOPIC_IDS = (
    "demo-transformer",
    "demo-python-pytorch",
    "demo-nlp-practice",
    None,
    "seed-unmapped-topic",
)
TOPIC_NAMES = {
    "demo-transformer": "Transformer 核心组件",
    "demo-python-pytorch": "Python 与 PyTorch 基础",
    "demo-nlp-practice": "NLP 实战流程",
    "seed-unmapped-topic": "未识别主题",
}
KNOWLEDGE_POINTS = {
    "demo-transformer": ("demo-embedding", "demo-attention", "demo-encoder"),
    "demo-python-pytorch": ("demo-tensor", "demo-autograd", "demo-dataloader"),
    "demo-nlp-practice": ("demo-tokenize", "demo-classifier", "demo-evaluate"),
}
KNOWLEDGE_POINT_NAMES = {
    "demo-embedding": "Embedding 表示",
    "demo-attention": "Scaled Dot-Product Attention",
    "demo-encoder": "Transformer Encoder",
    "demo-tensor": "张量与形状",
    "demo-autograd": "自动微分",
    "demo-dataloader": "Dataset 与 DataLoader",
    "demo-tokenize": "文本预处理",
    "demo-classifier": "文本分类器",
    "demo-evaluate": "损失与评估指标",
}
SCORE_BASES = {
    "demo-embedding": 48,
    "demo-attention": 57,
    "demo-encoder": 70,
    "demo-tensor": 67,
    "demo-autograd": 78,
    "demo-dataloader": 88,
    "demo-tokenize": 84,
    "demo-classifier": 59,
    "demo-evaluate": 74,
}


def stable_id(key: str) -> str:
    return str(uuid.uuid5(SEED_NAMESPACE, key))


def month_shift(month_start: date, offset: int) -> date:
    month_index = month_start.year * 12 + month_start.month - 1 + offset
    year, month_index = divmod(month_index, 12)
    return date(year, month_index + 1, 1)


def month_starts(end_date: date) -> list[date]:
    current = end_date.replace(day=1)
    return [month_shift(current, offset) for offset in range(-4, 1)]


def daily_counts(total: int, day_count: int, phase: int) -> list[int]:
    """Allocate a monthly total with repeatable peaks and exact total."""
    weights = [
        1 + ((day * 11 + phase * 7) % 7) + (2 if day in {7, 14, 21, 28} else 0)
        for day in range(1, day_count + 1)
    ]
    weight_total = sum(weights)
    raw = [total * weight / weight_total for weight in weights]
    counts = [floor(value) for value in raw]
    remainder = total - sum(counts)
    order = sorted(range(day_count), key=lambda index: raw[index] - counts[index], reverse=True)
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def weighted_pool(values: tuple[Any, ...], weights: tuple[int, ...]) -> list[Any]:
    return [value for value, weight in zip(values, weights, strict=True) for _ in range(weight)]


def build_turn_plan(today: date, workspace_id: str, user_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, datetime]]:
    month_targets = (96, 128, 164, 204, 246)
    student_cycle = [
        0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10, 11,
    ]
    hours = (8, 8, 9, 9, 10, 10, 11, 12, 13, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19, 19, 20, 21, 22, 23)
    levels = ("beginner", "intermediate", "advanced")
    modes = ("explain", "practice", "socratic", "review")
    topic_weights = (
        (31, 25, 21, 8, 5),
        (29, 28, 24, 9, 6),
        (26, 31, 28, 9, 7),
        (23, 34, 31, 10, 8),
        (20, 37, 34, 11, 9),
    )
    level_weights = (
        (48, 34, 18),
        (43, 36, 21),
        (39, 37, 24),
        (35, 38, 27),
        (31, 39, 30),
    )
    mode_weights = (
        (39, 27, 22, 12),
        (35, 29, 23, 13),
        (31, 32, 24, 13),
        (28, 35, 24, 13),
        (25, 37, 25, 13),
    )

    turns: list[dict[str, Any]] = []
    latest_by_conversation: dict[str, datetime] = {}
    global_index = 0
    starts = month_starts(today)
    for month_index, start in enumerate(starts):
        calendar_days = monthrange(start.year, start.month)[1]
        visible_days = min(calendar_days, today.day) if start.year == today.year and start.month == today.month else calendar_days
        target = month_targets[month_index]
        if visible_days < calendar_days:
            target = max(visible_days, round(target * visible_days / calendar_days))
        counts = daily_counts(target, visible_days, month_index)
        topic_pool = weighted_pool(TOPIC_IDS, topic_weights[month_index])
        level_pool = weighted_pool(levels, level_weights[month_index])
        mode_pool = weighted_pool(modes, mode_weights[month_index])

        for day_number, count in enumerate(counts, start=1):
            for ordinal in range(count):
                student_index = student_cycle[(global_index + day_number + month_index) % len(student_cycle)]
                user_id = user_ids[student_index]
                session_slot = (day_number + ordinal + month_index) % 3 + 1
                conversation_id = f"tqa-student-{student_index + 1:02d}-session-{session_slot}"
                hour = hours[(global_index * 3 + ordinal + month_index) % len(hours)]
                minute = (global_index * 7 + ordinal * 11) % 60
                created_at = datetime(start.year, start.month, day_number, hour, minute, tzinfo=timezone.utc).replace(tzinfo=None)
                topic_id = topic_pool[(global_index * 5 + ordinal + day_number) % len(topic_pool)]
                level = level_pool[(global_index + ordinal * 2 + month_index) % len(level_pool)]
                mode = mode_pool[(global_index + day_number + ordinal) % len(mode_pool)]
                has_error = (global_index + month_index * 7) % 31 in {4, 19}
                turn_id = stable_id(f"turn:{start.isoformat()}:{day_number}:{ordinal}:{student_index}")
                state = {
                    "context": {
                        "topic_id": topic_id,
                        "topic_name": TOPIC_NAMES.get(topic_id or "", ""),
                        "level": level,
                        "mode": mode,
                    },
                    "progress": None,
                    "exercise": None,
                }
                turns.append(
                    {
                        "id": turn_id,
                        "conversation_id": conversation_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "input_text": f"测试问题：请帮我理解{TOPIC_NAMES.get(topic_id or '', '这个知识点')}中的第 {ordinal + 1} 个关键概念。",
                        "result_text": "已完成测试会话记录，用于教师统计展示。",
                        "error_kind": "model_timeout" if has_error else None,
                        "error_message": "测试数据：模型响应超时" if has_error else None,
                        "learning_state_json": json.dumps(state, ensure_ascii=False),
                        "idempotency_key": f"tqa:{start.isoformat()}:{day_number}:{ordinal}:{student_index}",
                        "created_at": created_at,
                        "updated_at": created_at + timedelta(minutes=2),
                        "started_at": created_at,
                        "completed_at": created_at + timedelta(minutes=2),
                    }
                )
                latest_by_conversation[conversation_id] = max(latest_by_conversation.get(conversation_id, created_at), created_at)
                global_index += 1
    return turns, latest_by_conversation


def build_exercise_plan(today: date, workspace_id: str, user_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    topics = ("demo-transformer", "demo-python-pytorch", "demo-nlp-practice")
    for month_index, start in enumerate(month_starts(today)):
        visible_day = min(today.day, monthrange(start.year, start.month)[1]) if start == today.replace(day=1) else monthrange(start.year, start.month)[1]
        for student_index, user_id in enumerate(user_ids):
            for exercise_index in range(5):
                topic_id = topics[(student_index + exercise_index + month_index) % len(topics)]
                points = KNOWLEDGE_POINTS[topic_id]
                knowledge_point_id = points[(student_index * 2 + exercise_index + month_index) % len(points)]
                score = max(35, min(98, SCORE_BASES[knowledge_point_id] + ((student_index * 13 + exercise_index * 17 + month_index * 7) % 19) - 9))
                day_offset = (student_index * 2 + exercise_index + month_index * 3) % max(1, min(12, visible_day))
                exercise_hour = (9, 11, 13, 16, 19)[exercise_index]
                completed_at = datetime(start.year, start.month, max(1, visible_day - day_offset), exercise_hour, 20, tzinfo=timezone.utc).replace(tzinfo=None)
                month_key = f"{start.year}-{start.month:02d}"
                exercise_id = stable_id(f"exercise:{month_key}:{student_index}:{exercise_index}")
                question_id = stable_id(f"exercise-question:{month_key}:{student_index}:{exercise_index}")
                blueprint = {
                    "id": f"tqa-blueprint-{knowledge_point_id}",
                    "topic_id": topic_id,
                    "knowledge_point_id": knowledge_point_id,
                    "knowledge_point_ids": [knowledge_point_id],
                    "rubric": [
                        {"criterion": "概念准确", "weight": 1},
                        {"criterion": "方法完整", "weight": 1},
                    ],
                }
                matches = [
                    {"criterion": "概念准确", "criterion_index": 0, "achieved": score >= 60},
                    {"criterion": "方法完整", "criterion_index": 1, "achieved": score >= 75},
                ]
                conversation_id = f"tqa-student-{student_index + 1:02d}-session-3"
                sessions.append(
                    {
                        "id": exercise_id,
                        "conversation_id": conversation_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "topic_id": topic_id,
                        "mode": "practice",
                        "status": "completed",
                        "blueprint_snapshot_json": json.dumps(blueprint, ensure_ascii=False),
                        "created_at": completed_at - timedelta(minutes=12),
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                    }
                )
                questions.append(
                    {
                        "id": question_id,
                        "exercise_session_id": exercise_id,
                        "sequence": 1,
                        "question": f"请解释 {TOPIC_NAMES[topic_id]} 的“{KNOWLEDGE_POINT_NAMES[knowledge_point_id]}”关键机制。",
                        "rubric_json": json.dumps(blueprint["rubric"], ensure_ascii=False),
                        "status": "completed",
                        "created_at": completed_at - timedelta(minutes=10),
                    }
                )
                attempts.append(
                    {
                        "id": stable_id(f"exercise-attempt:{month_key}:{student_index}:{exercise_index}"),
                        "exercise_question_id": question_id,
                        "answer": "这是用于展示评分统计的测试答案。",
                        "attempt_number": 1,
                        "rubric_matches_json": json.dumps(matches, ensure_ascii=False),
                        "normalized_score": score,
                        "passed": score >= 60,
                        "feedback": "测试数据：根据评分点生成的练习反馈。",
                        "created_at": completed_at,
                    }
                )
                evidence.append(
                    {
                        "id": stable_id(f"learning-evidence:{month_key}:{student_index}:{exercise_index}"),
                        "exercise_session_id": exercise_id,
                        "exercise_question_id": question_id,
                        "blueprint_snapshot_json": json.dumps(blueprint, ensure_ascii=False),
                        "learner_answer": "这是用于展示学习证据的测试答案。",
                        "normalized_score": score,
                        "passed": score >= 60,
                        "completed_at": completed_at,
                    }
                )
    return sessions, questions, attempts, evidence


def build_guided_plan(today: date, workspace_id: str, user_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    topics = ("demo-transformer", "demo-python-pytorch", "demo-nlp-practice")
    visible_day = min(today.day, monthrange(today.year, today.month)[1])
    for student_index, user_id in enumerate(user_ids):
        topic_id = topics[(student_index + 1) % len(topics)]
        misconception_count = student_index % 4
        completed_at = datetime(today.year, today.month, max(1, visible_day - (student_index % 10)), 16, 10, tzinfo=timezone.utc).replace(tzinfo=None)
        state = {
            "stage": "completed",
            "learner_responses": ["测试回答"],
            "known_concepts": ["基础概念"],
            "misconceptions": [f"待澄清概念 {index + 1}" for index in range(misconception_count)],
            "last_question": "请总结本次学习内容。",
        }
        rows.append(
            {
                "id": stable_id(f"guided:{today.year}-{today.month:02d}:{student_index}"),
                "conversation_id": f"tqa-student-{student_index + 1:02d}-session-3",
                "workspace_id": workspace_id,
                "user_id": user_id,
                "topic_id": topic_id,
                "status": "completed",
                "objective": f"掌握{TOPIC_NAMES[topic_id]}的核心概念",
                "attempts": 2,
                "state_json": json.dumps(state, ensure_ascii=False),
                "blueprint_snapshot_json": json.dumps({"topic_id": topic_id}, ensure_ascii=False),
                "created_at": completed_at - timedelta(minutes=18),
                "updated_at": completed_at,
                "completed_at": completed_at,
            }
        )
    return rows


def seed(workspace_id: str) -> dict[str, int | str]:
    url = settings.NLP_AGENT_DATABASE_URL.strip()
    if not url:
        raise RuntimeError("NLP_AGENT_DATABASE_URL is required")
    engine = create_engine(url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
    today = datetime.now(timezone.utc).date()
    usernames = [f"tqa_student_{index + 1:02d}" for index in range(len(STUDENT_NAMES))]
    try:
        with engine.begin() as connection:
            workspace = connection.execute(text("SELECT id FROM nlp_workspaces WHERE id=:id AND status='active'"), {"id": workspace_id}).scalar_one_or_none()
            if workspace is None:
                raise RuntimeError(f"active workspace not found: {workspace_id}")
            role_id = connection.execute(text("SELECT id FROM nlp_roles WHERE code='student' AND status='active'"), {}).scalar_one_or_none()
            if role_id is None:
                raise RuntimeError("active student role not found")
            owner_id = connection.execute(text("SELECT id FROM nlp_users WHERE username='integration' AND status='active'"), {}).scalar_one_or_none()
            existing = {}
            for username in usernames:
                row = connection.execute(text("SELECT id,username,registration_source FROM nlp_users WHERE username=:username"), {"username": username}).mappings().first()
                if row is not None:
                    existing[username] = row
            user_ids: list[str] = []
            for index, (username, display_name) in enumerate(zip(usernames, STUDENT_NAMES, strict=True)):
                row = existing.get(username)
                if row is not None and row["registration_source"] != SEED_REGISTRATION_SOURCE:
                    raise RuntimeError(f"refusing to modify existing non-seed user: {username}")
                user_id = str(row["id"]) if row is not None else stable_id(f"user:{username}")
                user_ids.append(user_id)
                connection.execute(
                    text("""
                        INSERT IGNORE INTO nlp_users(id,username,password_hash,display_name,status,registration_source)
                        VALUES(:id,:username,:password_hash,:display_name,'active',:registration_source)
                    """),
                    {
                        "id": user_id,
                        "username": username,
                        "password_hash": "seeded-test-account-not-for-login",
                        "display_name": display_name,
                        "registration_source": SEED_REGISTRATION_SOURCE,
                    },
                )
                connection.execute(
                    text("""
                        INSERT INTO nlp_workspace_members(workspace_id,user_id,member_type,status)
                        VALUES(:workspace,:user,'member','active')
                        ON DUPLICATE KEY UPDATE status='active',member_type='member'
                    """),
                    {"workspace": workspace_id, "user": user_id},
                )
                connection.execute(
                    text("""
                        INSERT INTO nlp_user_roles(user_id,role_id,assigned_by_user_id)
                        VALUES(:user,:role,:assigned_by)
                        ON DUPLICATE KEY UPDATE expires_at=NULL
                    """),
                    {"user": user_id, "role": role_id, "assigned_by": owner_id},
                )

            turns, latest_by_conversation = build_turn_plan(today, workspace_id, user_ids)
            exercise_sessions, exercise_questions, attempts, evidence = build_exercise_plan(today, workspace_id, user_ids)
            guided_rows = build_guided_plan(today, workspace_id, user_ids)
            conversation_ids = set(latest_by_conversation) | {row["conversation_id"] for row in exercise_sessions} | {row["conversation_id"] for row in guided_rows}
            for conversation_id in sorted(conversation_ids):
                latest = latest_by_conversation.get(conversation_id, today)
                connection.execute(
                    text("""
                        INSERT IGNORE INTO nlp_conversations(id,workspace_id,owner_user_id,title,status,last_message_at,created_at,updated_at)
                        VALUES(:id,:workspace,:user,:title,'active',:last_message,:created_at,:updated_at)
                    """),
                    {
                        "id": conversation_id,
                        "workspace": workspace_id,
                        "user": user_ids[int(conversation_id.split("-")[2]) - 1],
                        "title": "教师问题统计测试会话",
                        "last_message": latest,
                        "created_at": latest - timedelta(days=150),
                        "updated_at": latest,
                    },
                )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_turns(
                        id,conversation_id,workspace_id,user_id,status,input_text,result_text,error_kind,error_message,
                        learning_state_json,idempotency_key,started_at,completed_at,created_at,updated_at
                    ) VALUES(
                        :id,:conversation_id,:workspace_id,:user_id,'completed',:input_text,:result_text,:error_kind,:error_message,
                        :learning_state_json,:idempotency_key,:started_at,:completed_at,:created_at,:updated_at
                    )
                """),
                turns,
            )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_exercise_sessions(
                        id,conversation_id,workspace_id,user_id,topic_id,mode,status,blueprint_snapshot_json,created_at,updated_at,completed_at
                    ) VALUES(
                        :id,:conversation_id,:workspace_id,:user_id,:topic_id,:mode,:status,:blueprint_snapshot_json,:created_at,:updated_at,:completed_at
                    )
                """),
                exercise_sessions,
            )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_exercise_questions(
                        id,exercise_session_id,sequence,question,rubric_json,status,created_at
                    ) VALUES(:id,:exercise_session_id,:sequence,:question,:rubric_json,:status,:created_at)
                """),
                exercise_questions,
            )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_exercise_attempts(
                        id,exercise_question_id,answer,attempt_number,rubric_matches_json,normalized_score,passed,feedback,created_at
                    ) VALUES(:id,:exercise_question_id,:answer,:attempt_number,:rubric_matches_json,:normalized_score,:passed,:feedback,:created_at)
                """),
                attempts,
            )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_learning_evidence(
                        id,exercise_session_id,exercise_question_id,blueprint_snapshot_json,learner_answer,normalized_score,passed,completed_at
                    ) VALUES(:id,:exercise_session_id,:exercise_question_id,:blueprint_snapshot_json,:learner_answer,:normalized_score,:passed,:completed_at)
                """),
                evidence,
            )
            connection.execute(
                text("""
                    INSERT IGNORE INTO nlp_guided_sessions(
                        id,conversation_id,workspace_id,user_id,topic_id,status,objective,attempts,state_json,blueprint_snapshot_json,created_at,updated_at,completed_at
                    ) VALUES(
                        :id,:conversation_id,:workspace_id,:user_id,:topic_id,:status,:objective,:attempts,:state_json,:blueprint_snapshot_json,:created_at,:updated_at,:completed_at
                    )
                """),
                guided_rows,
            )
        return {
            "workspace_id": workspace_id,
            "students": len(user_ids),
            "turns": len(turns),
            "exercise_sessions": len(exercise_sessions),
            "guided_sessions": len(guided_rows),
            "months": 5,
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="向教师问题统计的真实 MySQL 读模型写入本地测试数据")
    parser.add_argument("--workspace-id", default=TARGET_WORKSPACE, help="目标 workspace ID")
    args = parser.parse_args()
    result = seed(args.workspace_id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
