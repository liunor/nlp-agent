from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictTeacherModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeachingGoals(StrictTeacherModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    course_title: str = Field(default="NLP 基础课程", max_length=120)
    description: str = Field(default="", max_length=2_000)
    objectives: list[str] = Field(default_factory=list, max_length=20)
    focus_topics: list[str] = Field(default_factory=list, max_length=30)
    target_level: Literal["beginner", "intermediate", "advanced"] = "beginner"


class UpdateTeachingGoals(StrictTeacherModel):
    course_title: str = Field(max_length=120)
    description: str = Field(default="", max_length=2_000)
    objectives: list[str] = Field(default_factory=list, max_length=20)
    focus_topics: list[str] = Field(default_factory=list, max_length=30)
    target_level: Literal["beginner", "intermediate", "advanced"] = "beginner"


class KnowledgePoint(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    markdown: str = Field(default="", max_length=20_000)
    status: Literal["enabled", "disabled"] = "enabled"
    sort_order: int = Field(default=0, ge=0, le=10_000)


class CourseTopic(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    status: Literal["enabled", "disabled"] = "enabled"
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list, max_length=100)


def _validate_unique_catalog_ids(topics: list[CourseTopic]) -> None:
    topic_ids: set[str] = set()
    knowledge_point_ids: set[str] = set()
    for topic in topics:
        if topic.id in topic_ids:
            raise ValueError(f"主题 ID 必须唯一：{topic.id}")
        topic_ids.add(topic.id)
        for point in topic.knowledge_points:
            if point.id in knowledge_point_ids:
                raise ValueError(f"知识点 ID 必须唯一：{point.id}")
            knowledge_point_ids.add(point.id)


class ExerciseBlueprint(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    topic_id: str = Field(min_length=1, max_length=64)
    knowledge_point_id: str = Field(min_length=1, max_length=64)
    instructions: str = Field(min_length=1, max_length=4_000)
    question_type: str = Field(min_length=1, max_length=80)
    status: Literal["draft", "enabled", "disabled"] = "draft"
    rubric: list[dict[str, object]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_multi_question_shape(cls, value: object) -> object:
        """Read old persisted catalogues once; new responses never expose these fields."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "knowledge_point_id" not in data:
            points = data.pop("knowledge_point_ids", [])
            data["knowledge_point_id"] = points[0] if points else "legacy_unassigned"
        if "question_type" not in data:
            types = data.pop("question_types", [])
            data["question_type"] = types[0] if types else "简答"
        data.pop("question_count", None)
        # 蓝图不再绑定学习难度；保留读取旧目录的兼容性，并在下一次保存时清除该字段。
        data.pop("level", None)
        return data


class ReviewBlueprint(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    topic_id: str = Field(min_length=1, max_length=64)
    knowledge_point_id: str = Field(min_length=1, max_length=64)
    instructions: str = Field(min_length=1, max_length=4_000)
    exercise_blueprint_id: str | None = Field(default=None, max_length=64)
    status: Literal["draft", "enabled", "disabled"] = "draft"
    question_type: str = Field(min_length=1, max_length=80)
    rubric: list[dict[str, object]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_multi_question_shape(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "knowledge_point_id" not in data:
            points = data.pop("knowledge_point_ids", [])
            data["knowledge_point_id"] = points[0] if points else "legacy_unassigned"
        if "question_type" not in data:
            types = data.pop("question_types", [])
            data["question_type"] = types[0] if types else "简答"
        data.pop("question_count", None)
        return data


class GuidedBlueprint(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    topic_id: str = Field(min_length=1, max_length=64)
    knowledge_point_id: str = Field(min_length=1, max_length=64)
    guidance: str = Field(min_length=1, max_length=4_000)
    status: Literal["draft", "enabled", "disabled"] = "draft"


class TeacherCatalog(StrictTeacherModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    topics: list[CourseTopic] = Field(default_factory=list, max_length=100)
    exercise_blueprints: list[ExerciseBlueprint] = Field(default_factory=list, max_length=100)
    review_blueprints: list[ReviewBlueprint] = Field(default_factory=list, max_length=100)
    guided_blueprints: list[GuidedBlueprint] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "TeacherCatalog":
        _validate_unique_catalog_ids(self.topics)
        return self


class UpdateTeacherCatalog(StrictTeacherModel):
    topics: list[CourseTopic] = Field(default_factory=list, max_length=100)
    exercise_blueprints: list[ExerciseBlueprint] = Field(default_factory=list, max_length=100)
    review_blueprints: list[ReviewBlueprint] = Field(default_factory=list, max_length=100)
    guided_blueprints: list[GuidedBlueprint] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "UpdateTeacherCatalog":
        _validate_unique_catalog_ids(self.topics)
        return self


class TeacherBookNavigationItem(StrictTeacherModel):
    topic_id: str
    topic_name: str
    knowledge_point_id: str
    title: str
    sort_order: int = Field(ge=0)
    topic_status: Literal["enabled", "disabled"]
    knowledge_point_status: Literal["enabled", "disabled"]
    has_draft: bool = False
    has_published: bool = False
    revision: int = Field(default=0, ge=0)
    published_revision: int | None = Field(default=None, ge=0)


class LearningBookNavigationItem(StrictTeacherModel):
    topic_id: str
    topic_name: str
    knowledge_point_id: str
    title: str
    sort_order: int = Field(ge=0)
    revision: int = Field(ge=0)


class TeacherBookPage(StrictTeacherModel):
    workspace_id: str
    topic_id: str
    topic_name: str
    knowledge_point_id: str
    title: str
    draft_markdown: str = ""
    published_markdown: str | None = None
    revision: int = Field(default=0, ge=0)
    published_revision: int | None = Field(default=None, ge=0)
    updated_at: str | None = None


class LearningBookPage(StrictTeacherModel):
    workspace_id: str
    topic_id: str
    topic_name: str
    knowledge_point_id: str
    title: str
    content_markdown: str
    revision: int = Field(ge=0)


class TeacherBookAssetInput(StrictTeacherModel):
    asset_path: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=64)
    content_base64: str = Field(min_length=1, max_length=7_000_000)


class UpdateTeacherBookPage(StrictTeacherModel):
    content_markdown: str = Field(default="", max_length=200_000)
    expected_revision: int = Field(default=0, ge=0)
    assets: list[TeacherBookAssetInput] = Field(default_factory=list, max_length=50)


class PublishTeacherBookPage(StrictTeacherModel):
    expected_revision: int = Field(ge=0)


class TeacherBookImportPreviewRequest(StrictTeacherModel):
    file_name: str = Field(min_length=1, max_length=256)
    content_markdown: str = Field(max_length=200_000)


class TeacherBookImportApplyRequest(TeacherBookImportPreviewRequest):
    knowledge_point_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)
    assets: list[TeacherBookAssetInput] = Field(default_factory=list, max_length=50)


class TeacherBookImportPreview(StrictTeacherModel):
    file_name: str
    content_markdown: str
    removed_frameworks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TeacherBookArchiveImportPreviewRequest(StrictTeacherModel):
    file_name: str = Field(min_length=1, max_length=256)
    archive_base64: str = Field(min_length=1, max_length=16_000_000)


class TeacherBookArchiveItemPreview(StrictTeacherModel):
    topic_id: str
    knowledge_point_id: str
    title: str
    file_name: str
    action: Literal["create", "update", "unchanged"]
    expected_revision: int = Field(ge=0)
    current_markdown: str = ""
    content_markdown: str
    removed_frameworks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TeacherBookArchiveImportPreview(StrictTeacherModel):
    file_name: str
    format_version: int = Field(ge=1)
    title: str
    items: list[TeacherBookArchiveItemPreview] = Field(max_length=1000)
    asset_paths: list[str] = Field(default_factory=list, max_length=1000)
    omitted_knowledge_points: list[str] = Field(default_factory=list, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class TeacherBookArchiveImportApplyRequest(TeacherBookArchiveImportPreviewRequest):
    expected_revisions: dict[str, int] = Field(max_length=1000)
