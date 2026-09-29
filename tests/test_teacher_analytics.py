from datetime import date

from server.teacher.analytics import UNSELECTED_TOPIC, build_analytics, build_learning_analysis, build_monthly_analytics


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


def question(
    turn_id,
    *,
    topic_id=None,
    level="beginner",
    mode="explain",
    has_error=False,
    session="s1",
    user="u1",
    day="2026-01-01",
    hour=None,
    weekday=None,
    display_name=None,
    username=None,
    role_codes=None,
):
    return {
        "session_id": session,
        "user_id": user,
        "display_name": display_name,
        "username": username,
        "role_codes": ["student"] if role_codes is None else role_codes,
        "has_error": has_error,
        "topic_id": topic_id,
        "level": level,
        "mode": mode,
        "day": day,
        "hour": hour,
        "weekday": weekday,
    }


def evidence(topic_id, kp_ids, score, passed, *, user="u1", role_codes=None):
    return {
        "topic_id": topic_id,
        "mode": "practice",
        "knowledge_point_ids": kp_ids,
        "score": score,
        "passed": passed,
        "user_id": user,
        "role_codes": ["student"] if role_codes is None else role_codes,
    }


def criterion(topic_id, kp_ids, matches, *, user="u1", role_codes=None):
    return {
        "topic_id": topic_id,
        "knowledge_point_ids": kp_ids,
        "matches": matches,
        "user_id": user,
        "role_codes": ["student"] if role_codes is None else role_codes,
    }


def guided(topic_id, misconception_count, *, user="u1", role_codes=None):
    return {
        "topic_id": topic_id,
        "status": "completed",
        "misconception_count": misconception_count,
        "user_id": user,
        "role_codes": ["student"] if role_codes is None else role_codes,
    }


def test_summary_and_distributions_are_derived_from_structured_context():
    rows = [
        question("t1", topic_id="transformer", level="advanced", mode="explain"),
        question("t2", topic_id="transformer", level="beginner", mode="practice"),
        question("t3", topic_id=None, level="intermediate", mode="review", has_error=True, session="s2", user="u2"),
    ]
    result = build_analytics(rows, [], [], [], catalog())

    assert result["summary"]["questions"] == 3
    assert result["summary"]["sessions"] == 2
    assert result["summary"]["students"] == 2
    assert result["summary"]["error_questions"] == 1

    topics = {item["name"]: item["count"] for item in result["topic_distribution"]}
    assert topics == {"Transformer 与注意力": 2, UNSELECTED_TOPIC: 1}

    levels = {item["name"]: item["count"] for item in result["difficulty_distribution"]}
    assert levels == {"入门": 1, "进阶": 1, "深入": 1}

    modes = {item["name"]: item["count"] for item in result["mode_distribution"]}
    assert modes == {"讲解": 1, "练习": 1, "复习": 1}


def test_weak_topics_are_evidence_based():
    rows = [question("t1", topic_id="transformer")]
    evidence_rows = [
        evidence("transformer", ["attention"], 50, False),
        evidence("transformer", ["attention"], 90, True),
    ]
    guided_rows = [guided("transformer", 1)]
    result = build_analytics(rows, evidence_rows, [], guided_rows, catalog())

    weak = result["weak_topics"]
    assert len(weak) == 1
    assert weak[0]["topic_id"] == "transformer"
    assert weak[0]["topic"] == "Transformer 与注意力"
    assert weak[0]["exercises"] == 2
    assert weak[0]["average_score"] == 70.0
    assert weak[0]["pass_rate"] == 50.0
    assert weak[0]["misconceptions"] == 1
    # 50% pass rate is below the high-risk threshold.
    assert weak[0]["risk"] == "high"


def test_knowledge_point_stats_include_weak_criteria():
    criterion_rows = [
        criterion("transformer", ["attention"], [
            {"criterion": "概念准确", "criterion_index": 0, "achieved": True},
            {"criterion": "步骤完整", "criterion_index": 1, "achieved": False},
        ]),
        criterion("transformer", ["attention"], [
            {"criterion": "概念准确", "criterion_index": 0, "achieved": True},
            {"criterion": "步骤完整", "criterion_index": 1, "achieved": False},
        ]),
    ]
    evidence_rows = [evidence("transformer", ["attention"], 50, False)]
    result = build_analytics([], evidence_rows, criterion_rows, [], catalog())

    kp = result["knowledge_point_stats"][0]
    assert kp["knowledge_point_id"] == "attention"
    assert kp["name"] == "自注意力"
    assert kp["topic"] == "Transformer 与注意力"
    assert kp["exercises"] == 1

    weak = {item["criterion"]: item["hit_rate"] for item in kp["weak_criteria"]}
    # 步骤完整 hit 0/2, so it is the weakest criterion with a 0% hit rate.
    assert weak["步骤完整"] == 0.0


def test_no_raw_question_text_is_returned():
    result = build_analytics(
        [question("t1", topic_id="transformer")], [], [], [], catalog()
    )
    assert "question" not in result
    assert "questions" not in result  # raw text list removed; only summary aggregates remain
    assert "frequent_questions" not in result


def test_daily_question_trend_is_derived():
    rows = [
        question("t1", topic_id="transformer", day="2026-01-01"),
        question("t2", topic_id="transformer", day="2026-01-01"),
        question("t3", topic_id="transformer", day="2026-01-02"),
    ]
    result = build_analytics(rows, [], [], [], catalog())
    assert result["daily_questions"] == [
        {"date": "2026-01-01", "count": 2},
        {"date": "2026-01-02", "count": 1},
    ]


def test_topic_with_many_questions_but_no_evidence_is_not_low_risk():
    rows = [question(f"t{i}", topic_id="transformer") for i in range(6)]
    result = build_analytics(rows, [], [], [], catalog())
    assert result["weak_topics"][0]["risk"] == "medium"


def test_question_analytics_filters_to_student_scope_and_exposes_activity_metrics():
    rows = [
        question(
            "t1",
            topic_id="transformer",
            session="s1",
            user="u1",
            day="2026-01-01",
            hour=9,
            weekday=3,
            display_name="张三",
            username="zhangsan",
            role_codes=["student"],
        ),
        question(
            "t2",
            topic_id="transformer",
            session="s1",
            user="u1",
            day="2026-01-02",
            hour=10,
            weekday=4,
            display_name="张三",
            username="zhangsan",
            role_codes=["student"],
        ),
        question(
            "t3",
            topic_id=None,
            session="s2",
            user="u2",
            day="2026-01-02",
            hour=10,
            weekday=4,
            has_error=True,
            display_name="李四",
            username="lisi",
            role_codes=["guest"],
        ),
    ]

    result = build_analytics(rows, [], [], [], catalog())

    assert result["summary"] == {
        **result["summary"],
        "questions": 2,
        "students": 1,
        "sessions": 1,
        "active_days": 2,
        "error_questions": 0,
        "error_rate": 0.0,
        "questions_per_student": 2.0,
        "questions_per_session": 2.0,
        "contextualized_questions": 2,
        "context_coverage_rate": 100.0,
    }
    assert "student_role_users" not in result["summary"]
    assert "role_distribution" not in result
    students = {item["user_id"]: item for item in result["student_activity"]}
    assert students["u1"] == {
        "user_id": "u1",
        "display_name": "张三",
        "username": "zhangsan",
        "questions": 2,
        "sessions": 1,
        "active_days": 2,
        "error_questions": 0,
        "error_rate": 0.0,
        "questions_per_session": 2.0,
        "last_active": "2026-01-02",
        "top_topic": "Transformer 与注意力",
    }
    assert len(result["hourly_questions"]) == 24
    assert result["hourly_questions"][9] == {"hour": 9, "label": "09:00", "count": 1, "percentage": 50.0}
    assert result["hourly_questions"][10] == {"hour": 10, "label": "10:00", "count": 1, "percentage": 50.0}
    assert len(result["weekday_questions"]) == 7
    assert result["weekday_questions"][3] == {"weekday": 3, "label": "星期四", "count": 1, "percentage": 50.0}
    assert result["weekday_questions"][4] == {"weekday": 4, "label": "星期五", "count": 1, "percentage": 50.0}


def test_all_learning_analytics_streams_use_student_scope():
    student_matches = [{"criterion": "概念准确", "criterion_index": 0, "achieved": True}]
    teacher_matches = [{"criterion": "概念准确", "criterion_index": 0, "achieved": False}]
    result = build_analytics(
        [question("turn-student", user="student-1", role_codes=["student"])],
        [
            evidence("transformer", ["attention"], 90, True, user="student-1"),
            evidence("transformer", ["attention"], 20, False, user="teacher-1", role_codes=["teacher"]),
        ],
        [
            criterion("transformer", ["attention"], student_matches, user="student-1"),
            criterion("transformer", ["attention"], teacher_matches, user="teacher-1", role_codes=["teacher"]),
        ],
        [
            guided("transformer", 1, user="student-1"),
            guided("transformer", 9, user="teacher-1", role_codes=["teacher"]),
        ],
        catalog(),
        student_user_ids={"student-1"},
    )

    assert result["summary"]["exercises"] == 1
    assert result["summary"]["guided_sessions"] == 1
    assert result["weak_topics"][0]["exercises"] == 1
    assert result["weak_topics"][0]["misconceptions"] == 1
    assert result["knowledge_point_stats"][0]["weak_criteria"][0]["hit_rate"] == 100.0


def test_period_daily_trend_keeps_zero_activity_days_visible():
    result = build_analytics([], [], [], [], catalog(), period_days=7)

    assert len(result["daily_questions"]) == 7
    assert all(item["count"] == 0 for item in result["daily_questions"])


def test_period_daily_trend_uses_explicit_utc_date_window():
    result = build_analytics(
        [question("t1", day="2026-01-01"), question("t2", day="2026-01-03")],
        [], [], [], catalog(), period_days=3,
        period_start=date(2026, 1, 1), period_end=date(2026, 1, 3),
    )

    assert result["daily_questions"] == [
        {"date": "2026-01-01", "count": 1},
        {"date": "2026-01-02", "count": 0},
        {"date": "2026-01-03", "count": 1},
    ]


def test_monthly_question_statistics_cover_five_calendar_months():
    rows = [
        question("jan", day="2026-01-15", hour=9, user="student-1", role_codes=["student"], topic_id="transformer"),
        question("apr", day="2026-04-30", hour=18, user="student-1", role_codes=["student"], topic_id=None),
        question("may-1", day="2026-05-01", hour=10, user="student-1", role_codes=["student"], topic_id="transformer"),
        question("may-2", day="2026-05-20", hour=10, user="teacher-1", role_codes=["teacher"], topic_id="transformer"),
    ]

    result = build_monthly_analytics(
        rows,
        catalog(),
        period_end=date(2026, 5, 20),
        student_user_ids={"student-1"},
    )

    assert [item["month"] for item in result] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    assert result[0]["question_count"] == 1
    assert result[3]["question_count"] == 1
    assert result[4]["question_count"] == 1
    assert len(result[4]["daily_questions"]) == 20
    assert len(result[4]["hourly_questions"]) == 24
    assert result[4]["hourly_questions"][10]["count"] == 1
    assert result[4]["topic_distribution"] == [{"name": "Transformer 与注意力", "count": 1, "percentage": 100.0}]


def test_learning_analysis_builds_student_only_diagnoses_and_comparison_trend():
    catalog_data = {
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
    evidence_rows = [
        *[
            evidence("transformer", ["attention"], 45, False, user=f"student-{index % 2 + 1}")
            | {"completed_at": "2026-08-10T10:00:00+00:00"}
            for index in range(4)
        ],
        *[
            evidence("transformer", ["attention"], 92, True, user=f"student-{index % 2 + 1}")
            | {"completed_at": "2026-07-10T10:00:00+00:00"}
            for index in range(4)
        ],
        *[
            evidence("transformer", ["posenc"], 88, True, user=f"student-{index % 3 + 1}")
            | {"completed_at": "2026-08-11T10:00:00+00:00"}
            for index in range(4)
        ],
    ]
    criterion_rows = [
        criterion("transformer", ["attention"], [{"criterion": "定义域判断", "achieved": False}], user="student-1")
        | {"completed_at": "2026-08-10T10:00:00+00:00"},
        criterion("transformer", ["attention"], [{"criterion": "定义域判断", "achieved": False}], user="student-2")
        | {"completed_at": "2026-08-10T10:00:00+00:00"},
        criterion("transformer", ["attention"], [{"criterion": "定义域判断", "achieved": True}], user="student-1")
        | {"completed_at": "2026-08-10T10:00:00+00:00"},
        criterion("transformer", ["attention"], [{"criterion": "定义域判断", "achieved": False}], user="student-2")
        | {"completed_at": "2026-08-10T10:00:00+00:00"},
    ]
    result = build_learning_analysis(
        [question(f"question-{index}", topic_id="transformer", day="2026-08-10") for index in range(4)],
        evidence_rows,
        criterion_rows,
        catalog_data,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        student_user_ids={"student-1", "student-2", "student-3"},
    )

    attention = next(item for item in result["diagnoses"] if item["knowledge_point_id"] == "attention")
    assert attention["content_id"] == "transformer"
    assert attention["question_count"] == 4
    assert attention["student_count"] == 2
    assert attention["attempt_count"] == 4
    assert attention["correct_count"] == 0
    assert attention["error_count"] == 4
    assert attention["repeated_error_student_count"] == 2
    assert attention["question_examples"] == []
    assert attention["mastery_rate"] == 0.0
    assert attention["previous_mastery_rate"] == 100.0
    assert attention["trend"] == "down"
    assert attention["problem_type"] == "易错点集中"
    assert attention["data_sufficiency"] == "sufficient"
    assert result["conclusions"]["weak"]["knowledge_point_id"] == "attention"
    assert result["conclusions"]["declining"]["knowledge_point_id"] == "attention"
    assert result["conclusions"]["good"]["knowledge_point_id"] == "posenc"
    assert len(result["mastery_trend"]["months"]) == 5
    assert len(result["mastery_trend"]["series"]) == 2
    assert {item["name"]: item["count"] for item in result["problem_distribution"]}["易错点集中"] == 1


def test_learning_analysis_marks_small_samples_as_insufficient_instead_of_overclaiming():
    rows = [
        evidence("transformer", ["attention"], 20, False, user="student-1")
        | {"completed_at": "2026-08-10T10:00:00+00:00"},
    ]
    result = build_learning_analysis(
        [], rows, [], catalog(), period_start=date(2026, 8, 1), period_end=date(2026, 8, 31)
    )

    item = result["diagnoses"][0]
    assert item["data_sufficiency"] == "insufficient"
    assert item["problem_type"] == "数据不足，暂不判断"
    assert item["mastery_rate"] == 0.0
    assert result["conclusions"]["good"] is None


def test_learning_analysis_keeps_question_only_content_visible_as_insufficient():
    result = build_learning_analysis(
        [question(f"question-{index}", topic_id="transformer", day="2026-08-10") for index in range(6)],
        [],
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    assert len(result["diagnoses"]) == 2
    assert {item["question_count"] for item in result["diagnoses"]} == {0}
    assert all(item["problem_type"] == "数据不足，暂不判断" for item in result["diagnoses"])


def test_learning_analysis_counts_unique_exercise_questions_instead_of_topic_chat_questions():
    evidence_rows = [
        evidence("transformer", ["attention"], 40, False, user="student-1")
        | {"question_id": "exercise-1"},
        evidence("transformer", ["attention"], 50, False, user="student-2")
        | {"question_id": "exercise-1"},
        evidence("transformer", ["attention"], 80, True, user="student-1")
        | {"question_id": "exercise-2"},
        evidence("transformer", ["attention"], 70, True, user="student-2")
        | {"question_id": "exercise-3"},
    ]
    result = build_learning_analysis(
        [question(f"chat-{index}", topic_id="transformer") for index in range(20)],
        evidence_rows,
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    item = next(item for item in result["diagnoses"] if item["knowledge_point_id"] == "attention")
    assert item["question_count"] == 3


def test_learning_analysis_trend_uses_evidence_backed_points_before_placeholders():
    result = build_learning_analysis(
        [question(f"chat-{index}", topic_id="transformer") for index in range(8)],
        [
            evidence("transformer", ["attention"], 45, False, user="student-1"),
            evidence("transformer", ["attention"], 55, False, user="student-2"),
            evidence("transformer", ["attention"], 80, True, user="student-1"),
            evidence("transformer", ["attention"], 75, True, user="student-2"),
        ],
        [],
        catalog(),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )

    assert [item["knowledge_point_id"] for item in result["mastery_trend"]["series"]] == ["attention"]


def test_risk_thresholds_respect_exact_boundaries():
    def risk_at(passes: int, total: int) -> str:
        evidence_rows = [
            evidence("transformer", ["attention"], 100 if index < passes else 30, index < passes)
            for index in range(total)
        ]
        return build_analytics([], evidence_rows, [], [], catalog())["weak_topics"][0]["risk"]

    # A pass rate exactly at the floor must not fall into the higher band:
    # 60.0% is "medium" (not "high"), 80.0% is "low" (not "medium").
    assert risk_at(3, 5) == "medium"
    assert risk_at(4, 5) == "low"
