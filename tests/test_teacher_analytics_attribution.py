"""Exercise-level attribution semantics for teacher learning analysis.

This covers the "整题口径（仅标注）" decision: per-knowledge-point mastery is the
exercise-level pass rate, kept as-is but labelled.  It also guards the scope
``attempt_count`` against double-counting exercises that touch several points.
"""

from datetime import date

from server.teacher.analytics import build_learning_analysis


def catalog():
    return {
        "topics": [
            {
                "id": "transformer",
                "name": "Transformer 与注意力",
                "knowledge_points": [
                    {"id": "attention", "name": "自注意力"},
                    {"id": "posenc", "name": "位置编码"},
                ],
            }
        ]
    }


def evidence(topic_id, kp_ids, score, passed, *, user="u1"):
    return {
        "topic_id": topic_id,
        "mode": "practice",
        "knowledge_point_ids": kp_ids,
        "score": score,
        "passed": passed,
        "user_id": user,
        "role_codes": ["student"],
    }


def test_diagnosis_labelled_as_exercise_level_attribution():
    result = build_learning_analysis(
        [],
        [evidence("transformer", ["attention"], 90, True, user="student-1")],
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    assert all(item["mastery_basis"] == "exercise" for item in result["diagnoses"])
    assert result["diagnoses"][0]["mastery_rate"] == 100.0


def test_scope_attempt_count_counts_unique_exercises_not_point_pairs():
    # One exercise touching two knowledge points: both diagnoses report it, but
    # the scope attempt count must count it once.
    result = build_learning_analysis(
        [],
        [
            evidence("transformer", ["attention", "posenc"], 90, True, user="student-1"),
            evidence("transformer", ["attention"], 60, False, user="student-2"),
        ],
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    assert result["scope"]["attempt_count"] == 2
    by_id = {item["knowledge_point_id"]: item for item in result["diagnoses"]}
    # Per-point attempt counts still reflect attribution: attention carries both
    # exercises, posenc carries one.
    assert by_id["attention"]["attempt_count"] == 2
    assert by_id["posenc"]["attempt_count"] == 1


def test_scope_attempt_count_equals_unique_evidence_rows_when_every_point_is_single():
    result = build_learning_analysis(
        [],
        [
            evidence("transformer", ["attention"], 90, True, user="student-1"),
            evidence("transformer", ["attention"], 60, False, user="student-2"),
            evidence("transformer", ["posenc"], 70, True, user="student-1"),
        ],
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    assert result["scope"]["attempt_count"] == 3