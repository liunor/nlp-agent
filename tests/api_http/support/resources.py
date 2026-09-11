"""Small resource builders shared by the real HTTP API tests."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import httpx

from .database import MySqlProbe


@dataclass(frozen=True, slots=True)
class CreatedUser:
    username: str
    password: str
    user_id: str
    role: str


@dataclass(frozen=True, slots=True)
class CatalogSeed:
    workspace_id: str
    topic_id: str
    knowledge_point_id: str
    topic_name: str
    knowledge_point_name: str
    exercise_blueprint_id: str | None = None
    review_blueprint_id: str | None = None
    guided_blueprint_id: str | None = None


def create_user(
    admin_client: httpx.Client,
    *,
    role: str = "guest",
    prefix: str = "phase3user",
) -> CreatedUser:
    password = "Phase3-password-123!"
    username = f"{prefix}{uuid.uuid4().hex}"
    response = admin_client.post(
        "/api/v1/users",
        json={
            "username": username,
            "display_name": f"Phase 3 {role}",
            "password": password,
            "role_codes": [role],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return CreatedUser(
        username=username,
        password=password,
        user_id=str(payload["id"]),
        role=role,
    )


def create_workspace(
    client: httpx.Client,
    *,
    name: str,
    workspace_type: str = "organization",
) -> dict[str, str]:
    slug = f"phase3-{uuid.uuid4().hex}"
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "slug": slug, "type": workspace_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_classroom(
    client: httpx.Client,
    *,
    workspace_id: str,
    name: str,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/classrooms",
        json={"workspace_id": workspace_id, "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_workspace_member(
    client: httpx.Client,
    *,
    workspace_id: str,
    user_id: str,
    member_type: str = "member",
) -> dict[str, str]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"user_id": user_id, "member_type": member_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_session(client: httpx.Client, *, workspace_id: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/sessions",
        json={"workspace_id": workspace_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def seed_catalog(
    teacher_client: httpx.Client,
    *,
    workspace_id: str,
    prefix: str = "phase4",
    include_blueprints: bool = False,
) -> CatalogSeed:
    suffix = uuid.uuid4().hex[:8]
    topic_id = str(uuid.uuid4())
    knowledge_point_id = str(uuid.uuid4())
    topic_name = f"{prefix} Topic"
    knowledge_point_name = f"{prefix} Knowledge Point"
    catalog: dict[str, object] = {
        "topics": [
            {
                "id": topic_id,
                "name": topic_name,
                "description": "Deterministic Phase 4 topic",
                "status": "enabled",
                "knowledge_points": [
                    {
                        "id": knowledge_point_id,
                        "name": knowledge_point_name,
                        "markdown": "Q, K, and V are attention inputs.",
                        "status": "enabled",
                        "sort_order": 0,
                        "question_types": ["简答"],
                    }
                ],
            }
        ],
        "exercise_blueprints": [],
        "review_blueprints": [],
        "guided_blueprints": [],
    }
    exercise_blueprint_id = None
    review_blueprint_id = None
    guided_blueprint_id = None
    if include_blueprints:
        exercise_blueprint_id = str(uuid.uuid4())
        review_blueprint_id = str(uuid.uuid4())
        guided_blueprint_id = str(uuid.uuid4())
        catalog["exercise_blueprints"] = [
            {
                "id": exercise_blueprint_id,
                "name": "Phase 4 Exercise",
                "topic_id": topic_id,
                "knowledge_point_id": knowledge_point_id,
                "instructions": "Explain the role of Q, K, and V.",
                "question_type": "简答",
                "status": "draft",
                "rubric": [{"criterion": "核心概念正确", "weight": 100}],
            }
        ]
        catalog["review_blueprints"] = [
            {
                "id": review_blueprint_id,
                "name": "Phase 4 Review",
                "topic_id": topic_id,
                "knowledge_point_id": knowledge_point_id,
                "instructions": "Review the attention inputs.",
                "question_type": "简答",
                "status": "draft",
                "rubric": [{"criterion": "能够复述", "weight": 100}],
            }
        ]
        catalog["guided_blueprints"] = [
            {
                "id": guided_blueprint_id,
                "name": "Phase 4 Guided",
                "topic_id": topic_id,
                "knowledge_point_id": knowledge_point_id,
                "guidance": "Ask one Socratic question at a time.",
                "status": "draft",
            }
        ]
    response = teacher_client.put(
        f"/api/v1/teacher/catalog/{workspace_id}",
        json=catalog,
    )
    assert response.status_code == 200, response.text
    return CatalogSeed(
        workspace_id=workspace_id,
        topic_id=topic_id,
        knowledge_point_id=knowledge_point_id,
        topic_name=topic_name,
        knowledge_point_name=knowledge_point_name,
        exercise_blueprint_id=exercise_blueprint_id,
        review_blueprint_id=review_blueprint_id,
        guided_blueprint_id=guided_blueprint_id,
    )


def seed_teacher_ai_evidence(
    mysql_probe: MySqlProbe,
    *,
    workspace_id: str,
    student_user_ids: list[str],
    session_ids: list[str],
    topic_id: str,
    knowledge_point_id: str,
) -> None:
    """Seed only the persisted read model needed by the teacher AI report.

    The public HTTP suite deliberately does not start Chat/LLM Worker flows.
    These rows are synthetic evidence in the isolated MySQL database, not a
    shortcut around the HTTP authorization or teacher report request.
    """
    if len(student_user_ids) != len(session_ids) or len(student_user_ids) < 2:
        raise ValueError("teacher AI evidence needs at least two students and sessions")
    blueprint = {
        "id": str(uuid.uuid4()),
        "topic_id": topic_id,
        "knowledge_point_id": knowledge_point_id,
        "question_type": "简答",
    }
    for student_user_id, conversation_id in zip(student_user_ids, session_ids, strict=True):
        for index in range(2):
            exercise_session_id = str(uuid.uuid4())
            question_id = str(uuid.uuid4())
            evidence_id = str(uuid.uuid4())
            mysql_probe.execute(
                "INSERT INTO nlp_exercise_sessions "
                "(id,conversation_id,workspace_id,user_id,topic_id,mode,status,"
                "blueprint_snapshot_json,completed_at) "
                "VALUES(:id,:conversation_id,:workspace_id,:user_id,:topic_id,"
                "'practice','completed',:blueprint,UTC_TIMESTAMP(6))",
                id=exercise_session_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                user_id=student_user_id,
                topic_id=topic_id,
                blueprint=json.dumps(blueprint, ensure_ascii=False),
            )
            mysql_probe.execute(
                "INSERT INTO nlp_exercise_questions "
                "(id,exercise_session_id,sequence,question,rubric_json,status) "
                "VALUES(:id,:exercise_session_id,:sequence,:question,:rubric,'completed')",
                id=question_id,
                exercise_session_id=exercise_session_id,
                sequence=1,
                question=f"Synthetic Phase 4 question {index}",
                rubric=json.dumps([], ensure_ascii=False),
            )
            mysql_probe.execute(
                "INSERT INTO nlp_learning_evidence "
                "(id,exercise_session_id,exercise_question_id,blueprint_snapshot_json,"
                "learner_answer,normalized_score,passed,completed_at) "
                "VALUES(:id,:exercise_session_id,:exercise_question_id,:blueprint,"
                ":answer,:score,:passed,UTC_TIMESTAMP(6))",
                id=evidence_id,
                exercise_session_id=exercise_session_id,
                exercise_question_id=question_id,
                blueprint=json.dumps(blueprint, ensure_ascii=False),
                answer="Synthetic answer",
                score=40 if index == 0 else 60,
                passed=index == 1,
            )
