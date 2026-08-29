import pytest
from pydantic import ValidationError

from server.teacher.models import CourseTopic, ExerciseBlueprint, KnowledgePoint, TeacherCatalog
from server.teacher.service import TeacherService


def test_knowledge_point_defaults_to_the_supported_question_types():
    point = KnowledgePoint(id="point", name="知识点")

    assert point.question_types == ["简答", "选择题", "判断题", "填空题", "编程题", "代码阅读题", "计算题", "论述题"]


def test_knowledge_point_normalizes_custom_question_types():
    point = KnowledgePoint(id="point", name="知识点", question_types=[" 简答 ", "实验题", "实验题", " "])

    assert point.question_types == ["简答", "实验题"]


def test_knowledge_point_requires_one_non_blank_question_type():
    with pytest.raises(ValidationError, match="至少需要启用一种题型"):
        KnowledgePoint(id="point", name="知识点", question_types=[" "])


def test_enabled_blueprint_must_use_a_question_type_enabled_on_its_point():
    point = KnowledgePoint(id="point", name="知识点", question_types=["实验题"])
    topic = CourseTopic(id="topic", name="主题", knowledge_points=[point])
    blueprint = ExerciseBlueprint(
        id="blueprint",
        name="蓝图",
        topic_id="topic",
        knowledge_point_id="point",
        instructions="生成题目",
        question_type="选择题",
        status="enabled",
        rubric=[{"criterion": "概念准确", "weight": 100}],
    )

    with pytest.raises(ValueError, match="知识点已启用的题型"):
        TeacherService._validate_blueprint_links(TeacherCatalog(workspace_id="workspace", topics=[topic], exercise_blueprints=[blueprint]))
