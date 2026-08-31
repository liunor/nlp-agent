"""Deterministic, evidence-based aggregation for teacher analytics.

All inputs are structured rows produced by the MySQL read model
(``gateway.mysql_repository``) plus the teacher catalogue for name resolution.
No keyword-based topic/type classification happens here: topic, level and mode
come from the structured learning context already stored on each turn.

Student *question* text never reaches this module: ``list_question_turns``
deliberately omits ``input_text``, so the "student questions" view reports
aggregates only.  The exercise *question examples* surfaced by
``exercise_evidence_stats`` are distinct: they are teacher-authored practice
items, and their text is intentionally retained (bounded to three per knowledge
point) so teachers can recognize which exercise a diagnosis refers to.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

UNSELECTED_TOPIC = "未选择主题"
UNRECOGNIZED_TOPIC = "未识别主题"

LEVEL_LABELS = {"beginner": "入门", "intermediate": "进阶", "advanced": "深入"}
MODE_LABELS = {"explain": "讲解", "socratic": "引导", "practice": "练习", "review": "复习"}
WEEKDAY_LABELS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
LEARNING_ANALYSIS_PROBLEM_TYPES = (
    "概念掌握不足",
    "解题方法不熟",
    "易错点集中",
    "练习覆盖不足",
    "学习参与不足",
    "数据不足，暂不判断",
)
_DEFAULT_LEVEL = "beginner"
_DEFAULT_MODE = "explain"

# Diagnostic thresholds.  Grouped as named constants so boundary behaviour is
# testable, and the policy behind "risk", "trend" and "problem_type" is visible
# in one place instead of scattered as inline magic numbers.
_RISK_HIGH_PASS_RATE = 60
_RISK_MEDIUM_PASS_RATE = 80
_RISK_HIGH_MISCONCEPTIONS = 3
_RISK_MEDIUM_ERRORS = 2
_RISK_UNMEASURED_QUESTION_FLOOR = 5
_TREND_DELTA_PERCENT = 10
_DIAGNOSIS_MIN_ATTEMPTS = 4
_DIAGNOSIS_MIN_STUDENTS = 2
_COVERAGE_ATTEMPT_FLOOR = 5
_CONCEPT_MASTERY_LOW = 60
_METHOD_MASTERY_LOW = 80
_CONCERN_TREND_DOWN_PENALTY = 20
_CONCERN_PROBLEM_PENALTY = 10


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
    if (pass_rate is not None and pass_rate < _RISK_HIGH_PASS_RATE) or misconceptions >= _RISK_HIGH_MISCONCEPTIONS:
        return "high"
    if (pass_rate is not None and pass_rate < _RISK_MEDIUM_PASS_RATE) or errors >= _RISK_MEDIUM_ERRORS:
        return "medium"
    # A topic students ask about a lot but never practice is unmeasured, not
    # "low risk": it deserves attention instead of being silently cleared.
    if exercises == 0 and questions >= _RISK_UNMEASURED_QUESTION_FLOOR:
        return "medium"
    return "low"


def _average_score(score_sum: int, count: int) -> float | None:
    return round(score_sum / count, 2) if count else None


def _concern_score(mastery_rate: float | None, trend: str, problem_type: str) -> float:
    """Ranking heuristic only — not a field-grade metric.

    Combines the complement of mastery on a 0-100 scale with fixed penalties for
    a declining trend and an actionable problem type.  The magnitude has no
    standalone meaning; it only orders the diagnoses list.
    """
    return (
        (100 - (mastery_rate or 0))
        + (_CONCERN_TREND_DOWN_PENALTY if trend == "down" else 0)
        + (_CONCERN_PROBLEM_PENALTY if problem_type not in {"—", "数据不足，暂不判断"} else 0)
    )


def _role_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        return sorted({item.strip() for item in value.split(",") if item.strip()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted({str(item).strip() for item in value if str(item).strip()})
    return []


def _time_distribution(counter: Counter, total: int, *, labels: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    result = []
    keys = range(len(labels)) if labels is not None else range(24)
    for key in keys:
        label = labels[key] if labels is not None else f"{key:02d}:00"
        result.append({"hour" if labels is None else "weekday": key, "label": label, "count": counter[key], "percentage": _percent(counter[key], total)})
    return result


def _is_student(row: dict[str, Any]) -> bool:
    role_codes = row.get("role_codes") or row.get("roles")
    return "student" in _role_codes(role_codes)


def _in_student_scope(row: dict[str, Any], student_user_ids: set[str] | None) -> bool:
    if student_user_ids is not None:
        user_id = row.get("user_id")
        return user_id is not None and str(user_id) in student_user_ids
    # Direct callers may provide already-filtered read-model rows without a
    # user id. If role metadata is present, still enforce it at this boundary.
    return not ("role_codes" in row or "roles" in row) or _is_student(row)


def _row_date(row: dict[str, Any]) -> date | None:
    value = row.get("day")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _previous_month(month_start: date) -> date:
    return (month_start - timedelta(days=1)).replace(day=1)


def _analysis_row_date(row: dict[str, Any]) -> date | None:
    """Read the completion day from either SQL or test read-model rows."""
    value = row.get("day") or row.get("completed_at") or row.get("created_at")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _analysis_in_window(row: dict[str, Any], start: date, end: date) -> bool:
    row_day = _analysis_row_date(row)
    return row_day is None or start <= row_day <= end


def _analysis_period_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [row for row in rows if _analysis_in_window(row, start, end)]


def filter_period_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    """Return rows whose completion day falls within ``[start, end]`` inclusive.

    Rows without a usable day are kept (they are treated as in-window), matching
    ``build_learning_analysis``.  Exposed so ``TeacherService.analytics`` can
    derive the period-scoped overview rows from a single broad evidence/criterion
    fetch instead of issuing a second, narrower query.
    """
    return _analysis_period_rows(rows, start, end)


def _analysis_evidence_aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "topic_id": None,
            "student_ids": set(),
            "attempt_count": 0,
            "correct_count": 0,
            "failed_count": 0,
            "question_ids": set(),
            "failed_student_attempts": Counter(),
            "score_sum": 0,
            "question_examples": [],
        }
    )
    for row in rows:
        topic_id = str(row["topic_id"]) if row.get("topic_id") else None
        for knowledge_point_id in row.get("knowledge_point_ids") or []:
            key = str(knowledge_point_id)
            stat = result[key]
            stat["topic_id"] = stat["topic_id"] or topic_id
            if row.get("user_id"):
                stat["student_ids"].add(str(row["user_id"]))
            if row.get("question_id"):
                stat["question_ids"].add(str(row["question_id"]))
            stat["attempt_count"] += 1
            stat["correct_count"] += 1 if row.get("passed") else 0
            if not row.get("passed"):
                stat["failed_count"] += 1
                if row.get("user_id"):
                    stat["failed_student_attempts"][str(row["user_id"])] += 1
            stat["score_sum"] += int(row.get("score") or 0)
            if row.get("question") and not any(
                example.get("question_id") == row.get("question_id")
                for example in stat["question_examples"]
            ):
                stat["question_examples"].append(
                    {
                        "question_id": str(row.get("question_id") or ""),
                        "question": str(row["question"]),
                        "score": int(row.get("score") or 0),
                        "passed": bool(row.get("passed")),
                    }
                )
    return result


def _analysis_criteria_aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    result: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for row in rows:
        for knowledge_point_id in row.get("knowledge_point_ids") or []:
            point = result[str(knowledge_point_id)]
            for match in row.get("matches") or []:
                if not isinstance(match, dict):
                    continue
                key = _criterion_key(match)
                failed, total = point.get(key, [0, 0])
                point[key] = [failed + (0 if match.get("achieved") else 1), total + 1]
    return result


def _analysis_recommendation(problem_type: str, name: str, mastery_rate: float | None, error_criterion: str | None) -> tuple[str, str]:
    rate = "暂无" if mastery_rate is None else f"{mastery_rate:g}%"
    if problem_type == "概念掌握不足":
        return (
            f"“{name}”当前掌握率为 {rate}，基础概念的正确应用仍不稳定。",
            "补充核心概念和边界条件示例，再安排 2～3 道由易到难的变式题。",
        )
    if problem_type == "解题方法不熟":
        return (
            f"“{name}”当前掌握率为 {rate}，学生已经接触过内容，但解题路径还不够稳定。",
            "把解题过程拆成关键步骤，安排一道示范题和 2～3 道同构练习。",
        )
    if problem_type == "易错点集中":
        criterion = f"，主要集中在“{error_criterion}”" if error_criterion else ""
        return (
            f"“{name}”当前掌握率为 {rate}，重复错误较集中{criterion}。",
            "针对高频错误评分点做一次辨析讲解，并安排 2～3 道基础变式题巩固。",
        )
    if problem_type == "练习覆盖不足":
        return (
            f"“{name}”已有学习信号，但练习覆盖还不足以确认稳定掌握（当前掌握率 {rate}）。",
            "增加不同题型和难度的练习覆盖，再观察下一周期的掌握率变化。",
        )
    if problem_type == "学习参与不足":
        return (
            f"“{name}”现有结果不差，但参与学生或作答次数偏少，结论需要继续观察。",
            "在下一次教学中安排一次低门槛练习，扩大参与面后再判断。",
        )
    return (
        f"“{name}”当前样本量偏少，暂不判断掌握情况。",
        "先补充练习或等待更多学生作答，再查看下一周期的诊断结果。",
    )


def build_learning_analysis(
    question_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    criterion_rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    *,
    period_start: date,
    period_end: date,
    student_user_ids: set[str] | None = None,
    month_count: int = 5,
) -> dict[str, Any]:
    """Build the content-diagnosis report used by the teacher learning page."""
    topic_names, kp_names, kp_topics = _index_catalog(catalog)
    normalized_student_ids = {str(user_id) for user_id in student_user_ids} if student_user_ids is not None else None
    questions = [row for row in question_rows if _in_student_scope(row, normalized_student_ids)]
    evidence = [row for row in evidence_rows if _in_student_scope(row, normalized_student_ids)]
    criteria = [row for row in criterion_rows if _in_student_scope(row, normalized_student_ids)]

    period_days = max(1, (period_end - period_start).days + 1)
    previous_end = period_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    current_evidence = _analysis_period_rows(evidence, period_start, period_end)
    # The diagnosis trend compares against the immediately preceding windows of
    # the same length (not calendar months).  ``mastery_trend`` below uses
    # calendar months, so the two series can disagree on short windows; that is
    # intentional — "trend" is a quick before/after delta, "mastery_trend" is
    # the longer month-over-month history.
    previous_evidence = _analysis_period_rows(evidence, previous_start, previous_end)
    current_criteria = _analysis_period_rows(criteria, period_start, period_end)
    current_by_kp = _analysis_evidence_aggregate(current_evidence)
    previous_by_kp = _analysis_evidence_aggregate(previous_evidence)
    criteria_by_kp = _analysis_criteria_aggregate(current_criteria)
    question_count_by_topic: Counter = Counter(str(row["topic_id"]) for row in questions if row.get("topic_id"))
    question_students_by_topic: dict[str, set[str]] = defaultdict(set)
    for row in questions:
        if row.get("topic_id") and row.get("user_id"):
            question_students_by_topic[str(row["topic_id"])].add(str(row["user_id"]))
    for topic in catalog.get("topics", []) if isinstance(catalog, dict) else []:
        if not isinstance(topic, dict):
            continue
        topic_id = str(topic.get("id") or "")
        if not topic_id or not question_count_by_topic[topic_id]:
            continue
        for point in topic.get("knowledge_points", []) or []:
            if not isinstance(point, dict) or not point.get("id"):
                continue
            point_id = str(point["id"])
            if point_id not in current_by_kp:
                current_by_kp[point_id] = {
                    "topic_id": topic_id,
                    "student_ids": question_students_by_topic[topic_id].copy(),
                    "attempt_count": 0,
                    "correct_count": 0,
                    "failed_count": 0,
                    "question_ids": set(),
                    "failed_student_attempts": Counter(),
                    "score_sum": 0,
                    "question_examples": [],
                }
    current_students = {
        str(row["user_id"])
        for row in [*questions, *current_evidence]
        if row.get("user_id")
    }

    diagnoses: list[dict[str, Any]] = []
    for knowledge_point_id, current in current_by_kp.items():
        attempts = current["attempt_count"]
        students = len(current["student_ids"])
        # Per-point mastery is the exercise-level pass rate of every exercise
        # touching this point (整题级).  A "passed" exercise credits all of its
        # knowledge points, so this is attribution granularity, not per-rubric
        # criterion evaluation.
        mastery_rate = _percent(current["correct_count"], attempts) if attempts else None
        previous = previous_by_kp.get(knowledge_point_id)
        previous_rate = (
            _percent(previous["correct_count"], previous["attempt_count"])
            if previous and previous["attempt_count"]
            else None
        )
        if previous_rate is None or mastery_rate is None:
            trend = "stable"
        elif mastery_rate - previous_rate <= -_TREND_DELTA_PERCENT:
            trend = "down"
        elif mastery_rate - previous_rate >= _TREND_DELTA_PERCENT:
            trend = "up"
        else:
            trend = "stable"

        criterion_stats = criteria_by_kp.get(knowledge_point_id, {})
        weak_criteria = [
            {"criterion": key, "error_rate": _percent(failed, total)}
            for key, (failed, total) in sorted(
                criterion_stats.items(), key=lambda item: (item[1][0] / item[1][1] if item[1][1] else 0), reverse=True
            )[:3]
        ]
        concentrated = bool(
            weak_criteria
            and criterion_stats[weak_criteria[0]["criterion"]][1] > 2
            and weak_criteria[0]["error_rate"] >= 50
        )
        sufficient = attempts >= _DIAGNOSIS_MIN_ATTEMPTS and students >= _DIAGNOSIS_MIN_STUDENTS
        if not sufficient:
            problem_type = "数据不足，暂不判断"
        elif concentrated:
            problem_type = "易错点集中"
        elif mastery_rate is not None and mastery_rate < _CONCEPT_MASTERY_LOW:
            problem_type = "概念掌握不足"
        elif mastery_rate is not None and mastery_rate < _METHOD_MASTERY_LOW:
            problem_type = "解题方法不熟"
        elif attempts < max(_COVERAGE_ATTEMPT_FLOOR, students * 2):
            problem_type = "练习覆盖不足"
        else:
            problem_type = "—"

        point_name = kp_names.get(knowledge_point_id, UNRECOGNIZED_TOPIC)
        topic_id = current["topic_id"]
        topic_name = topic_names.get(str(topic_id), kp_topics.get(knowledge_point_id, UNRECOGNIZED_TOPIC))
        question_count = len(current["question_ids"]) or attempts
        error_criterion = weak_criteria[0]["criterion"] if weak_criteria else None
        conclusion, action = _analysis_recommendation(problem_type, point_name, mastery_rate, error_criterion)
        concern = _concern_score(mastery_rate, trend, problem_type)
        diagnoses.append(
            {
                "content_id": str(topic_id or "unrecognized"),
                "content_name": topic_name,
                "knowledge_point_id": knowledge_point_id,
                "knowledge_point_name": point_name,
                "question_count": question_count,
                "student_count": students,
                "attempt_count": attempts,
                "correct_count": current["correct_count"],
                "error_count": current["failed_count"],
                "repeated_error_student_count": sum(
                    count >= 2 for count in current["failed_student_attempts"].values()
                ),
                "mastery_rate": mastery_rate,
                # Signals that ``mastery_rate``/``average_score`` are exercise-
                # level attributions, not rubric-criterion evaluations.
                "mastery_basis": "exercise",
                "previous_mastery_rate": previous_rate,
                "trend": trend,
                "problem_type": problem_type,
                "data_sufficiency": "sufficient" if sufficient else "insufficient",
                "average_score": _average_score(current["score_sum"], attempts),
                "weak_criteria": weak_criteria,
                "question_examples": current["question_examples"][:3],
                "concern_score": round(concern, 2),
                "recommendation": {"conclusion": conclusion, "action": action},
            }
        )
    diagnoses.sort(key=lambda item: (-item["concern_score"], item["knowledge_point_name"]))

    weak = next(
        (
            item
            for item in diagnoses
            if item["data_sufficiency"] == "sufficient" and item["mastery_rate"] is not None and item["mastery_rate"] < _CONCEPT_MASTERY_LOW
        ),
        None,
    )
    declining = next((item for item in diagnoses if item["trend"] == "down"), None)
    good = next((item for item in reversed(diagnoses) if item["data_sufficiency"] == "sufficient" and item["mastery_rate"] is not None and item["mastery_rate"] >= _METHOD_MASTERY_LOW and item["trend"] != "down"), None)

    problem_counts = Counter(item["problem_type"] for item in diagnoses if item["problem_type"] != "—")
    problem_total = sum(problem_counts.values())
    problem_distribution = [
        {"name": problem_type, "count": problem_counts[problem_type], "percentage": _percent(problem_counts[problem_type], problem_total)}
        for problem_type in LEARNING_ANALYSIS_PROBLEM_TYPES
    ]

    current_month = period_end.replace(day=1)
    months: list[date] = []
    for _ in range(max(1, month_count)):
        months.insert(0, current_month)
        current_month = _previous_month(current_month)
    top_ids = [
        item["knowledge_point_id"]
        for item in diagnoses
        if item["data_sufficiency"] == "sufficient"
    ][:5]
    trend_series: list[dict[str, Any]] = []
    for knowledge_point_id in top_ids:
        values: list[float | None] = []
        for month_start in months:
            month_end = date(month_start.year, month_start.month, monthrange(month_start.year, month_start.month)[1])
            month_rows = _analysis_period_rows(evidence, month_start, min(month_end, period_end))
            monthly_stat = _analysis_evidence_aggregate(month_rows).get(knowledge_point_id)
            values.append(_percent(monthly_stat["correct_count"], monthly_stat["attempt_count"]) if monthly_stat and monthly_stat["attempt_count"] else None)
        item = next(item for item in diagnoses if item["knowledge_point_id"] == knowledge_point_id)
        trend_series.append({"knowledge_point_id": knowledge_point_id, "name": item["knowledge_point_name"], "values": values})

    return {
        "scope": {
            "period_days": period_days,
            "period_label": f"近 {period_days} 天",
            "role_label": "学生",
            "student_count": len(current_students),
            # Unique evidence rows in the period, not the per-point sum: an
            # exercise touching several knowledge points must count once here.
            "attempt_count": len(current_evidence),
        },
        "conclusions": {"weak": weak, "declining": declining, "good": good},
        "diagnoses": diagnoses,
        "problem_distribution": problem_distribution,
        "mastery_trend": {
            "months": [{"month": item.strftime("%Y-%m"), "label": f"{item.year}年{item.month:02d}月"} for item in months],
            "series": trend_series,
        },
    }


def build_monthly_analytics(
    question_rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    *,
    period_end: date | None = None,
    student_user_ids: set[str] | None = None,
    month_count: int = 5,
) -> list[dict[str, Any]]:
    """Build calendar-month snapshots for the teacher question comparison charts."""
    topic_names, _, _ = _index_catalog(catalog)
    normalized_student_ids = {str(user_id) for user_id in student_user_ids} if student_user_ids is not None else None
    rows = [row for row in question_rows if _in_student_scope(row, normalized_student_ids)]
    end_date = period_end or datetime.now(timezone.utc).date()
    current_month = end_date.replace(day=1)
    starts: list[date] = []
    for _ in range(max(1, month_count)):
        starts.insert(0, current_month)
        current_month = _previous_month(current_month)

    result: list[dict[str, Any]] = []
    for start in starts:
        calendar_end = date(start.year, start.month, monthrange(start.year, start.month)[1])
        visible_end = min(calendar_end, end_date)
        month_rows = [
            row for row in rows
            if (row_date := _row_date(row)) is not None and start <= row_date <= visible_end
        ]
        topic_counter: Counter = Counter()
        difficulty_counter: Counter = Counter()
        mode_counter: Counter = Counter()
        hourly_counter: Counter = Counter()
        daily_counter: Counter = Counter()
        for row in month_rows:
            topic_counter[_topic_name(row.get("topic_id"), topic_names)] += 1
            difficulty_counter[LEVEL_LABELS.get(row.get("level"), LEVEL_LABELS[_DEFAULT_LEVEL])] += 1
            mode_counter[MODE_LABELS.get(row.get("mode"), MODE_LABELS[_DEFAULT_MODE])] += 1
            if isinstance(row.get("hour"), int) and 0 <= row["hour"] <= 23:
                hourly_counter[row["hour"]] += 1
            row_date = _row_date(row)
            if row_date is not None:
                daily_counter[row_date.isoformat()] += 1

        days_in_view = max(0, (visible_end - start).days + 1)
        daily_questions = [
            {
                "day": day_number,
                "date": (start + timedelta(days=day_number - 1)).isoformat(),
                "count": daily_counter.get((start + timedelta(days=day_number - 1)).isoformat(), 0),
            }
            for day_number in range(1, days_in_view + 1)
        ]
        result.append(
            {
                "month": start.strftime("%Y-%m"),
                "label": f"{start.year}年{start.month:02d}月",
                "question_count": len(month_rows),
                "topic_distribution": _distribution(topic_counter, len(month_rows)),
                "difficulty_distribution": _distribution(difficulty_counter, len(month_rows)),
                "mode_distribution": _distribution(mode_counter, len(month_rows)),
                "daily_questions": daily_questions,
                "hourly_questions": _time_distribution(hourly_counter, len(month_rows)),
            }
        )
    return result


def build_analytics(
    question_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    criterion_rows: list[dict[str, Any]],
    guided_rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    *,
    period_days: int | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    student_user_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Aggregate structured learning rows into the teacher overview payload.

    ``question_rows``: from ``list_question_turns`` (no question text).
    ``evidence_rows``: from ``exercise_evidence_stats``.
    ``criterion_rows``: from ``exercise_criterion_stats``.
    ``guided_rows``: from ``guided_session_stats``.
    ``catalog``: the ``catalog`` dict from ``get_teaching_catalog``.
    """
    topic_names, kp_names, kp_topics = _index_catalog(catalog)
    normalized_student_ids = {str(user_id) for user_id in student_user_ids} if student_user_ids is not None else None
    question_rows = [row for row in question_rows if _in_student_scope(row, normalized_student_ids)]
    evidence_rows = [row for row in evidence_rows if _in_student_scope(row, normalized_student_ids)]
    criterion_rows = [row for row in criterion_rows if _in_student_scope(row, normalized_student_ids)]
    guided_rows = [row for row in guided_rows if _in_student_scope(row, normalized_student_ids)]

    # --- question summary + distributions -------------------------------
    question_count = len(question_rows)
    sessions = {str(row["session_id"]) for row in question_rows if row.get("session_id")}
    students = {str(row["user_id"]) for row in question_rows if row.get("user_id")}
    error_questions = sum(1 for row in question_rows if row.get("has_error"))

    topic_counter: Counter = Counter()
    difficulty_counter: Counter = Counter()
    mode_counter: Counter = Counter()
    daily_counter: Counter = Counter()
    hourly_counter: Counter = Counter()
    weekday_counter: Counter = Counter()
    question_by_topic: Counter = Counter()
    error_by_topic: Counter = Counter()
    student_stats: dict[str, dict[str, Any]] = {}
    student_topics: dict[str, Counter] = defaultdict(Counter)
    for row in question_rows:
        topic_id = row.get("topic_id")
        topic_counter[_topic_name(topic_id, topic_names)] += 1
        difficulty_counter[LEVEL_LABELS.get(row.get("level"), LEVEL_LABELS[_DEFAULT_LEVEL])] += 1
        mode_counter[MODE_LABELS.get(row.get("mode"), MODE_LABELS[_DEFAULT_MODE])] += 1
        day = row.get("day")
        if day:
            daily_counter[str(day)] += 1
        hour = row.get("hour")
        if isinstance(hour, int) and 0 <= hour <= 23:
            hourly_counter[hour] += 1
        weekday = row.get("weekday")
        if isinstance(weekday, int) and 0 <= weekday <= 6:
            weekday_counter[weekday] += 1
        if topic_id:
            question_by_topic[str(topic_id)] += 1
            if row.get("has_error"):
                error_by_topic[str(topic_id)] += 1
        user_id = row.get("user_id")
        if user_id:
            key = str(user_id)
            stat = student_stats.setdefault(
                key,
                {
                    "user_id": key,
                    "display_name": str(row.get("display_name") or key),
                    "username": str(row["username"]) if row.get("username") else None,
                    "questions": 0,
                    "sessions": set(),
                    "active_days": set(),
                    "error_questions": 0,
                    "last_active": None,
                },
            )
            stat["questions"] += 1
            stat["sessions"].add(str(row["session_id"])) if row.get("session_id") else None
            stat["active_days"].add(str(day)) if day else None
            stat["error_questions"] += 1 if row.get("has_error") else 0
            if day and (stat["last_active"] is None or str(day) > stat["last_active"]):
                stat["last_active"] = str(day)
            student_topics[key][_topic_name(topic_id, topic_names)] += 1

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

    student_activity = []
    for user_id, stat in student_stats.items():
        sessions_count = len(stat["sessions"])
        top_topic = student_topics[user_id].most_common(1)
        student_activity.append(
            {
                "user_id": stat["user_id"],
                "display_name": stat["display_name"],
                "username": stat["username"],
                "questions": stat["questions"],
                "sessions": sessions_count,
                "active_days": len(stat["active_days"]),
                "error_questions": stat["error_questions"],
                "error_rate": _percent(stat["error_questions"], stat["questions"]),
                "questions_per_session": _average_score(stat["questions"], sessions_count) or 0.0,
                "last_active": stat["last_active"],
                "top_topic": top_topic[0][0] if top_topic else UNSELECTED_TOPIC,
            }
        )
    student_activity.sort(key=lambda item: (-item["questions"], -item["active_days"], item["display_name"]))

    peak_day = max(daily_counter.items(), key=lambda item: (item[1], item[0])) if daily_counter else None
    peak_hour = max(hourly_counter.items(), key=lambda item: (item[1], -item[0])) if hourly_counter else None
    contextualized_questions = sum(1 for row in question_rows if row.get("topic_id"))

    if period_days is None:
        daily_questions = [{"date": day, "count": count} for day, count in sorted(daily_counter.items())]
    else:
        end_date = period_end or datetime.now(timezone.utc).date()
        start_date = period_start or end_date - timedelta(days=max(1, period_days) - 1)
        daily_questions = [
            {"date": (start_date + timedelta(days=offset)).isoformat(), "count": daily_counter.get((start_date + timedelta(days=offset)).isoformat(), 0)}
            for offset in range(max(1, period_days))
        ]

    return {
        "summary": {
            "questions": question_count,
            "sessions": len(sessions),
            "students": len(students),
            "active_days": len(daily_counter),
            "error_questions": error_questions,
            "error_rate": _percent(error_questions, question_count),
            "questions_per_student": _average_score(question_count, len(students)) or 0.0,
            "questions_per_session": _average_score(question_count, len(sessions)) or 0.0,
            "contextualized_questions": contextualized_questions,
            "context_coverage_rate": _percent(contextualized_questions, question_count),
            "exercises": exercises_total,
            "exercise_pass_rate": _percent(passes_total, exercises_total),
            "guided_sessions": len(guided_rows),
        },
        "topic_distribution": _distribution(topic_counter, question_count),
        "difficulty_distribution": _distribution(difficulty_counter, question_count),
        "mode_distribution": _distribution(mode_counter, question_count),
        "daily_questions": daily_questions,
        "hourly_questions": _time_distribution(hourly_counter, question_count),
        "weekday_questions": _time_distribution(weekday_counter, question_count, labels=WEEKDAY_LABELS),
        "peak_day": {"date": peak_day[0], "count": peak_day[1]} if peak_day else None,
        "peak_hour": {"hour": peak_hour[0], "label": f"{peak_hour[0]:02d}:00", "count": peak_hour[1]} if peak_hour else None,
        "student_activity": student_activity,
        "weak_topics": weak_topics,
        "knowledge_point_stats": knowledge_point_stats,
    }
