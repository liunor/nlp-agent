"""Deterministic, evidence-based aggregation for teacher analytics.

The teacher "student questions" module reports aggregates only; it never
returns raw question text.  All inputs are structured rows produced by the
MySQL read model (``gateway.mysql_repository``) plus the teacher catalogue for
name resolution.  No keyword-based topic/type classification happens here:
topic, level and mode come from the structured learning context already stored
on each turn.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

UNSELECTED_TOPIC = "未选择主题"
UNRECOGNIZED_TOPIC = "未识别主题"

LEVEL_LABELS = {"beginner": "入门", "intermediate": "进阶", "advanced": "深入"}
MODE_LABELS = {"explain": "讲解", "socratic": "引导", "practice": "练习", "review": "复习"}
_DEFAULT_LEVEL = "beginner"
_DEFAULT_MODE = "explain"


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _distribution(counter: Counter, total: int) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count, "percentage": _percent(count, total)}
        for name, count in counter.most_common()
    ]


def _index_catalog(catalog: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build topic-id and knowledge-point-id name maps from the teacher catalogue."""
    topics = catalog.get("topics", []) if isinstance(catalog, dict) else []
    topic_names: dict[str, str] = {}
    kp_names: dict[str, str] = {}
    kp_topics: dict[str, str] = {}
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id", ""))
        topic_name = str(topic.get("name") or topic_id)
        topic_names[topic_id] = topic_name
        for point in topic.get("knowledge_points", []) or []:
            if not isinstance(point, dict):
                continue
            point_id = str(point.get("id", ""))
            kp_names[point_id] = str(point.get("name") or point_id)
            kp_topics[point_id] = topic_name
    return topic_names, kp_names, kp_topics


def _topic_name(topic_id: Any, topic_names: dict[str, str]) -> str:
    if not topic_id:
        return UNSELECTED_TOPIC
    return topic_names.get(str(topic_id), UNRECOGNIZED_TOPIC)


def _criterion_key(match: dict[str, Any]) -> str:
    criterion = str(match.get("criterion") or "").strip()
    if criterion:
        return criterion
    index = match.get("criterion_index", 0)
    return f"评分点{int(index) + 1}" if isinstance(index, int) else "评分点"


def _risk(questions: int, exercises: int, pass_rate: float | None, misconceptions: int, errors: int) -> str:
    if (pass_rate is not None and pass_rate < 60) or misconceptions >= 3:
        return "high"
    if (pass_rate is not None and pass_rate < 80) or errors >= 2:
        return "medium"
    # A topic students ask about a lot but never practice is unmeasured, not
    # "low risk": it deserves attention instead of being silently cleared.
    if exercises == 0 and questions >= 5:
        return "medium"
    return "low"


def _average_score(score_sum: int, count: int) -> float | None:
    return round(score_sum / count, 2) if count else None


def build_analytics(
    question_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    criterion_rows: list[dict[str, Any]],
    guided_rows: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate structured learning rows into the teacher overview payload.

    ``question_rows``: from ``list_question_turns`` (no question text).
    ``evidence_rows``: from ``exercise_evidence_stats``.
    ``criterion_rows``: from ``exercise_criterion_stats``.
    ``guided_rows``: from ``guided_session_stats``.
    ``catalog``: the ``catalog`` dict from ``get_teaching_catalog``.
    """
    topic_names, kp_names, kp_topics = _index_catalog(catalog)

    # --- question summary + distributions -------------------------------
    question_count = len(question_rows)
    sessions = {str(row["session_id"]) for row in question_rows if row.get("session_id")}
    students = {str(row["user_id"]) for row in question_rows if row.get("user_id")}
    error_questions = sum(1 for row in question_rows if row.get("has_error"))

    topic_counter: Counter = Counter()
    difficulty_counter: Counter = Counter()
    mode_counter: Counter = Counter()
    daily_counter: Counter = Counter()
    question_by_topic: Counter = Counter()
    error_by_topic: Counter = Counter()
    for row in question_rows:
        topic_id = row.get("topic_id")
        topic_counter[_topic_name(topic_id, topic_names)] += 1
        difficulty_counter[LEVEL_LABELS.get(row.get("level"), LEVEL_LABELS[_DEFAULT_LEVEL])] += 1
        mode_counter[MODE_LABELS.get(row.get("mode"), MODE_LABELS[_DEFAULT_MODE])] += 1
        day = row.get("day")
        if day:
            daily_counter[str(day)] += 1
        if topic_id:
            question_by_topic[str(topic_id)] += 1
            if row.get("has_error"):
                error_by_topic[str(topic_id)] += 1

    # --- practice evidence by topic -------------------------------------
    evidence_by_topic: dict[str, dict[str, Any]] = defaultdict(lambda: {"exercises": 0, "score_sum": 0, "passes": 0})
    evidence_by_kp: dict[str, dict[str, Any]] = defaultdict(lambda: {"exercises": 0, "score_sum": 0, "passes": 0})
    for row in evidence_rows:
        score = int(row.get("score") or 0)
        passed = bool(row.get("passed"))
        topic_id = row.get("topic_id")
        if topic_id:
            stat = evidence_by_topic[str(topic_id)]
            stat["exercises"] += 1
            stat["score_sum"] += score
            if passed:
                stat["passes"] += 1
        for kp_id in row.get("knowledge_point_ids") or []:
            kp_stat = evidence_by_kp[str(kp_id)]
            kp_stat["exercises"] += 1
            kp_stat["score_sum"] += score
            if passed:
                kp_stat["passes"] += 1

    # --- rubric criterion hit-rates by knowledge point ------------------
    criterion_by_kp: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in criterion_rows:
        for kp_id in row.get("knowledge_point_ids") or []:
            kp_id = str(kp_id)
            for match in row.get("matches") or []:
                if not isinstance(match, dict):
                    continue
                key = _criterion_key(match)
                hit, total = criterion_by_kp[kp_id][key]
                criterion_by_kp[kp_id][key] = [hit + (1 if match.get("achieved") else 0), total + 1]

    # --- guided misconceptions by topic ----------------------------------
    misconception_by_topic: Counter = Counter()
    for row in guided_rows:
        if row.get("topic_id"):
            misconception_by_topic[str(row["topic_id"])] += int(row.get("misconception_count") or 0)

    # --- assemble weak topics --------------------------------------------
    all_topics = (
        set(question_by_topic)
        | set(error_by_topic)
        | set(evidence_by_topic)
        | set(misconception_by_topic)
    )
    weak_topics: list[dict[str, Any]] = []
    for topic_id in all_topics:
        evidence = evidence_by_topic.get(topic_id, {"exercises": 0, "score_sum": 0, "passes": 0})
        exercises = evidence["exercises"]
        pass_rate = _percent(evidence["passes"], exercises) if exercises else None
        weak_topics.append(
            {
                "topic_id": topic_id,
                "topic": topic_names.get(topic_id, UNRECOGNIZED_TOPIC),
                "questions": question_by_topic.get(topic_id, 0),
                "errors": error_by_topic.get(topic_id, 0),
                "exercises": exercises,
                "average_score": _average_score(evidence["score_sum"], exercises),
                "pass_rate": pass_rate,
                "misconceptions": misconception_by_topic.get(topic_id, 0),
                "risk": _risk(question_by_topic.get(topic_id, 0), exercises, pass_rate, misconception_by_topic.get(topic_id, 0), error_by_topic.get(topic_id, 0)),
            }
        )
    risk_order = {"high": 0, "medium": 1, "low": 2}
    weak_topics.sort(key=lambda item: (risk_order[item["risk"]], -item["questions"], item["average_score"] if item["average_score"] is not None else 101))

    # --- assemble knowledge-point stats -----------------------------------
    knowledge_point_stats: list[dict[str, Any]] = []
    for kp_id, evidence in evidence_by_kp.items():
        exercises = evidence["exercises"]
        pass_rate = _percent(evidence["passes"], exercises) if exercises else None
        criterion = criterion_by_kp.get(kp_id, {})
        weak_criteria = [
            {"criterion": key, "hit_rate": _percent(hit, total)}
            for key, (hit, total) in sorted(
                criterion.items(), key=lambda item: (item[1][0] / item[1][1] if item[1][1] else 1.0)
            )[:3]
        ]
        knowledge_point_stats.append(
            {
                "knowledge_point_id": kp_id,
                "name": kp_names.get(kp_id, UNRECOGNIZED_TOPIC),
                "topic": kp_topics.get(kp_id, UNRECOGNIZED_TOPIC),
                "exercises": exercises,
                "average_score": _average_score(evidence["score_sum"], exercises),
                "pass_rate": pass_rate,
                "weak_criteria": weak_criteria,
            }
        )
    knowledge_point_stats.sort(key=lambda item: (-item["exercises"], item["average_score"] if item["average_score"] is not None else 101))

    exercises_total = sum(stat["exercises"] for stat in evidence_by_topic.values())
    passes_total = sum(stat["passes"] for stat in evidence_by_topic.values())

    return {
        "summary": {
            "questions": question_count,
            "sessions": len(sessions),
            "students": len(students),
            "error_questions": error_questions,
            "exercises": exercises_total,
            "exercise_pass_rate": _percent(passes_total, exercises_total),
            "guided_sessions": len(guided_rows),
        },
        "topic_distribution": _distribution(topic_counter, question_count),
        "difficulty_distribution": _distribution(difficulty_counter, question_count),
        "mode_distribution": _distribution(mode_counter, question_count),
        "daily_questions": [{"date": day, "count": count} for day, count in sorted(daily_counter.items())],
        "weak_topics": weak_topics,
        "knowledge_point_stats": knowledge_point_stats,
    }
