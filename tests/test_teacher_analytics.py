from server.teacher.analytics import UNSELECTED_TOPIC, build_analytics


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


def question(turn_id, *, topic_id=None, level="beginner", mode="explain", has_error=False, session="s1", user="u1", day="2026-01-01"):
    return {
        "session_id": session,
        "user_id": user,
        "has_error": has_error,
        "topic_id": topic_id,
        "level": level,
        "mode": mode,
        "day": day,
    }


def evidence(topic_id, kp_ids, score, passed):
    return {"topic_id": topic_id, "mode": "practice", "knowledge_point_ids": kp_ids, "score": score, "passed": passed}


def criterion(topic_id, kp_ids, matches):
    return {"topic_id": topic_id, "knowledge_point_ids": kp_ids, "matches": matches}


def guided(topic_id, misconception_count):
    return {"topic_id": topic_id, "status": "completed", "misconception_count": misconception_count}


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
