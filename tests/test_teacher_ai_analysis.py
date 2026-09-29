import json

import pytest
from langchain_core.messages import AIMessage

from server.teacher.ai_analysis import (
    LearningAnalysisAICache,
    build_ai_analysis_material,
    generate_ai_analysis,
    learning_analysis_ai_cache,
    validate_ai_analysis_response,
)
from server.teacher.models import TeacherAIAnalysisRequest
from server.teacher.service import TeacherService


def diagnosis(**overrides):
    value = {
        "content_id": "calculus",
        "content_name": "高等数学",
        "knowledge_point_id": "monotonicity",
        "knowledge_point_name": "函数单调性",
        "question_count": 12,
        "student_count": 38,
        "attempt_count": 126,
        "correct_count": 53,
        "mastery_rate": 42.0,
        "previous_mastery_rate": 56.0,
        "trend": "down",
        "problem_type": "概念掌握不足",
        "data_sufficiency": "sufficient",
        "average_score": 42.0,
        "weak_criteria": [{"criterion": "定义域判断", "error_rate": 68.0}],
        "error_count": 73,
        "repeated_error_student_count": 24,
        "question_examples": [
            {"question_id": "q-1", "question": "判断函数在给定区间的单调性。", "score": 20, "passed": False}
        ],
        "concern_score": 88,
        "recommendation": {"conclusion": "规则结论", "action": "规则建议"},
    }
    return {**value, **overrides}


def test_ai_material_is_aggregated_and_contains_no_student_identity():
    material = build_ai_analysis_material(
        {
            "goals": {"course_title": "高等数学"},
            "learning_analysis": {
                "scope": {"period_label": "近 30 天", "role_label": "学生"},
                "diagnoses": [diagnosis()],
            },
        },
        course_id="all",
        content_scope="all",
    )

    assert material["course"] == "高等数学"
    assert material["student_role"] == "学生"
    content = material["contents"][0]
    assert content["mastery_rate"] == 0.42
    assert content["previous_mastery_rate"] == 0.56
    assert content["repeated_error_students"] == 24
    serialized = json.dumps(material, ensure_ascii=False)
    assert "student-1" not in serialized
    assert "user_id" not in serialized


def test_ai_material_skips_insufficient_catalog_placeholders_when_selecting_top_items():
    material = build_ai_analysis_material(
        {
            "learning_analysis": {
                "scope": {"period_label": "近 30 天"},
                "diagnoses": [
                    *[
                        diagnosis(
                            knowledge_point_id=f"placeholder-{index}",
                            knowledge_point_name=f"目录占位项 {index}",
                            data_sufficiency="insufficient",
                            attempt_count=0,
                            mastery_rate=None,
                        )
                        for index in range(5)
                    ],
                    diagnosis(knowledge_point_id="evidence-backed", knowledge_point_name="有证据知识点"),
                ],
            }
        }
    )

    assert [item["knowledge_point_id"] for item in material["contents"]] == ["evidence-backed"]


def test_ai_response_uses_backend_evidence_and_discards_unbound_suggestions():
    source = [diagnosis()]
    result = validate_ai_analysis_response(
        {
            "summary": "学生主要需要回顾定义域与单调区间的关系。",
            "diagnoses": [
                {
                    "knowledge_point_id": "monotonicity",
                    "level": "high",
                    "problem": "定义域判断存在共性混淆。",
                    "evidence": ["掌握率 99%"],
                    "suggestions": ["回顾定义域与单调区间的关系"],
                    "confidence": "high",
                    "data_gaps": [],
                },
                {
                    "knowledge_point_id": "unknown",
                    "level": "high",
                    "problem": "不应展示",
                    "evidence": ["没有后端绑定证据"],
                    "suggestions": ["不应展示"],
                    "confidence": "high",
                    "data_gaps": [],
                },
                {
                    "knowledge_point_id": "monotonicity",
                    "level": "high",
                    "problem": "没有证据的建议",
                    "evidence": [],
                    "suggestions": ["不应展示"],
                    "confidence": "high",
                    "data_gaps": [],
                },
            ],
        },
        source,
    )

    assert len(result["diagnoses"]) == 1
    item = result["diagnoses"][0]
    assert "42%" in item["evidence"][0]
    assert "99%" not in " ".join(item["evidence"])
    assert item["evidence"] == [
        "掌握率 42%；上期掌握率 56%；错误 73 次；重复错误学生 24 人；题目 12 道；涉及学生 38 人；作答 126 次。"
    ]


def test_ai_response_discards_diagnosis_with_only_blank_suggestions():
    with pytest.raises(ValueError, match="模型没有返回可绑定后端证据的诊断"):
        validate_ai_analysis_response(
            {
                "summary": "需要继续观察。",
                "diagnoses": [
                    {
                        "knowledge_point_id": "monotonicity",
                        "level": "medium",
                        "problem": "存在学习问题。",
                        "evidence": ["掌握率 42%"],
                        "suggestions": ["", "  "],
                        "confidence": "medium",
                        "data_gaps": [],
                    }
                ],
            },
            [diagnosis()],
        )


@pytest.mark.asyncio
async def test_model_failure_returns_rule_fallback_without_raising():
    class BrokenModel:
        async def ainvoke(self, _messages):
            raise RuntimeError("DeepSeek unavailable")

    result = await generate_ai_analysis({"contents": [diagnosis()]}, model=BrokenModel())

    assert result["source"] == "rules"
    assert result["status"] == "failed"
    assert result["diagnoses"][0]["knowledge_point_id"] == "monotonicity"
    assert "规则" in result["message"]


@pytest.mark.asyncio
async def test_valid_deepseek_json_is_normalized_to_teacher_safe_result():
    class FakeModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "summary": "本周期重点关注函数单调性。",
                        "diagnoses": [
                            {
                                "knowledge_point_id": "monotonicity",
                                "level": "high",
                                "problem": "学生对定义域与单调区间的关系理解不足。",
                                "evidence": ["掌握率 42%"],
                                "suggestions": ["回顾定义域与单调区间的关系", "安排基础变式题"],
                                "confidence": "high",
                                "data_gaps": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )

    result = await generate_ai_analysis({"contents": [diagnosis()]}, model=FakeModel())

    assert result["status"] == "completed"
    assert result["source"] == "deepseek"
    assert result["diagnoses"][0]["suggestions"] == ["回顾定义域与单调区间的关系", "安排基础变式题"]


@pytest.mark.asyncio
async def test_malformed_model_json_returns_rule_fallback():
    class MalformedModel:
        async def ainvoke(self, _messages):
            return AIMessage(content="不是 JSON")

    result = await generate_ai_analysis({"contents": [diagnosis()]}, model=MalformedModel())

    assert result["status"] == "failed"
    assert result["source"] == "rules"
    assert result["diagnoses"][0]["suggestions"]


@pytest.mark.asyncio
async def test_service_caches_completed_report_and_force_refresh_bypasses_cache(monkeypatch):
    service = TeacherService()
    service.require_teacher = lambda *_args, **_kwargs: None
    calls = 0

    async def fake_analytics(*_args, **_kwargs):
        return {
            "goals": {"course_title": "高等数学"},
            "learning_analysis": {
                "scope": {
                    "period_days": 31,
                    "period_label": "2026-08",
                    "role_label": "学生",
                    "student_count": 38,
                    "attempt_count": 126,
                },
                "diagnoses": [diagnosis()],
            },
        }

    async def fake_generate(_material):
        nonlocal calls
        calls += 1
        return {
            "status": "completed",
            "source": "deepseek",
            "message": "",
            "model_id": "deepseek-v4-flash",
            "summary": "已完成分析。",
            "diagnoses": [],
        }

    monkeypatch.setattr(service, "analytics", fake_analytics)
    monkeypatch.setattr("server.teacher.service.generate_ai_analysis", fake_generate)
    learning_analysis_ai_cache.clear()
    body = TeacherAIAnalysisRequest(
        workspace_id="default",
        course_id="all",
        content_scope="all",
        start_date="2026-08-01",
        end_date="2026-08-31",
    )

    first = await service.ai_analysis(object(), object(), "default", body)
    second = await service.ai_analysis(object(), object(), "default", body)
    refreshed = await service.ai_analysis(object(), object(), "default", body.model_copy(update={"force_refresh": True}))

    assert calls == 2
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert refreshed["cache_hit"] is False


@pytest.mark.asyncio
async def test_service_ai_analysis_uses_the_selected_period_days(monkeypatch):
    service = TeacherService()
    service.require_teacher = lambda *_args, **_kwargs: None
    captured = {}

    async def fake_analytics(*args, **kwargs):
        captured["days"] = args[3]
        return {
            "goals": {"course_title": "高等数学"},
            "learning_analysis": {
                "scope": {"period_days": 60, "period_label": "近 60 天", "role_label": "学生", "student_count": 2, "attempt_count": 8},
                "diagnoses": [diagnosis()],
            },
        }

    async def fake_generate(_material):
        return {"status": "completed", "source": "deepseek", "message": "", "model_id": "test", "summary": "已完成分析。", "diagnoses": []}

    monkeypatch.setattr(service, "analytics", fake_analytics)
    monkeypatch.setattr("server.teacher.service.generate_ai_analysis", fake_generate)
    learning_analysis_ai_cache.clear()

    await service.ai_analysis(
        object(),
        object(),
        "default",
        TeacherAIAnalysisRequest(workspace_id="default", course_id="all", content_scope="all", period_days=60),
    )

    assert captured["days"] == 60


def test_error_type_vocabulary_maps_only_semantic_correspondences():
    material = build_ai_analysis_material(
        {
            "learning_analysis": {
                "scope": {"period_label": "近 30 天"},
                "diagnoses": [
                    diagnosis(problem_type="概念掌握不足", knowledge_point_id="concept"),
                    diagnosis(problem_type="解题方法不熟", knowledge_point_id="method"),
                    diagnosis(problem_type="易错点集中", knowledge_point_id="concentrated"),
                    diagnosis(problem_type="练习覆盖不足", knowledge_point_id="coverage"),
                    diagnosis(problem_type="学习参与不足", knowledge_point_id="participation"),
                ],
            }
        }
    )

    by_id = {item["knowledge_point_id"]: item for item in material["contents"]}
    assert by_id["concept"]["error_type"] == "概念理解不足"
    assert by_id["method"]["error_type"] == "方法不熟"
    # 错误／覆盖／参与模式不等于认知原因，规则侧不得臆测为“计算错误/前置知识不足”。
    assert by_id["concentrated"]["error_type"] == "易错点集中"
    assert by_id["coverage"]["error_type"] == "练习覆盖不足"
    assert by_id["participation"]["error_type"] == "学习参与不足"


def test_rule_fallback_text_uses_analytics_recommendation_as_single_source():
    material = build_ai_analysis_material(
        {
            "learning_analysis": {
                "scope": {"period_label": "近 30 天"},
                "diagnoses": [diagnosis(recommendation={"conclusion": "单一来源结论", "action": "单一来源建议"})],
            }
        }
    )

    content = material["contents"][0]
    assert content["rule_problem"] == "单一来源结论"
    assert content["rule_suggestion"] == "单一来源建议"


def test_ai_cache_evicts_oldest_entry_once_bounded():
    cache = LearningAnalysisAICache(ttl_seconds=60, max_items=2)
    cache.set("a", {"n": 1})
    cache.set("b", {"n": 2})
    cache.set("c", {"n": 3})

    assert cache.get("a") is None
    assert cache.get("b") == {"n": 2}
    assert cache.get("c") == {"n": 3}
