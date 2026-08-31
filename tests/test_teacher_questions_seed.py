import json
from collections import Counter
from datetime import date

from scripts.seed_teacher_questions import KNOWLEDGE_POINTS, build_exercise_plan


def test_seed_exercises_cover_all_catalog_knowledge_points_across_five_months():
    students = [f"student-{index}" for index in range(12)]
    sessions, questions, attempts, evidence = build_exercise_plan(
        date(2026, 8, 30), "workspace-1", students
    )

    knowledge_point_ids = [
        json.loads(row["blueprint_snapshot_json"])["knowledge_point_ids"][0]
        for row in evidence
    ]
    counts = Counter(knowledge_point_ids)
    expected = {point_id for points in KNOWLEDGE_POINTS.values() for point_id in points}

    assert len(sessions) == 12 * 5 * 5
    assert len(questions) == len(sessions)
    assert len(attempts) == len(sessions)
    assert len(evidence) == len(sessions)
    assert {row["user_id"] for row in sessions} == set(students)
    assert set(counts) == expected
    assert min(counts.values()) >= 20
    assert {row["completed_at"].strftime("%Y-%m") for row in evidence} == {
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
    }
