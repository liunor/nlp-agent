from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


# Teacher notes on individual knowledge points are short classroom observations,
# not free-form documents.  Bounding each value keeps the annotations blob small
# and forces the frontend to enforce the same cap before the round-trip.
MAX_ANALYSIS_NOTE_LENGTH = 2_000
MAX_ANALYSIS_NOTES = 200


def _validate_analysis_notes(notes: dict[str, str]) -> dict[str, str]:
    if len(notes) > MAX_ANALYSIS_NOTES:
        raise ValueError(f"备注数量不能超过 {MAX_ANALYSIS_NOTES} 条")
    for key, value in notes.items():
        if len(value) > MAX_ANALYSIS_NOTE_LENGTH:
            raise ValueError(f"知识点“{key}”的备注不能超过 {MAX_ANALYSIS_NOTE_LENGTH} 个字符")
    return notes


class TeacherAnalysisAnnotations(StrictTeacherModel):
    """Per-workspace teacher bookmarks on the learning-analysis view."""

    workspace_id: str = Field(min_length=1, max_length=128)
    focused: list[str] = Field(default_factory=list, max_length=200)
    ignored: list[str] = Field(default_factory=list, max_length=200)
    notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("notes")
    @classmethod
    def _notes_within_limit(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_analysis_notes(value)


class UpdateTeacherAnalysisAnnotations(StrictTeacherModel):
    focused: list[str] = Field(default_factory=list, max_length=200)
    ignored: list[str] = Field(default_factory=list, max_length=200)
    notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("notes")
    @classmethod
    def _notes_within_limit(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_analysis_notes(value)


class TeacherAIAnalysisRequest(StrictTeacherModel):
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    course_id: str = Field(default="all", min_length=1, max_length=128)
    content_scope: str = Field(default="all", min_length=1, max_length=160)
    start_date: date | None = None
    end_date: date | None = None
    period_days: int | None = Field(default=None, ge=1, le=365)
    force_refresh: bool = False

    @model_validator(mode="after")
    def _validate_period(self) -> "TeacherAIAnalysisRequest":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date 和 end_date 必须同时提供")
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self


DEFAULT_QUESTION_TYPES = ("简答", "选择题", "判断题", "填空题", "编程题", "代码阅读题", "计算题", "论述题")


class KnowledgePoint(StrictTeacherModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    markdown: str = Field(default="", max_length=20_000)
    status: Literal["enabled", "disabled"] = "enabled"
    sort_order: int = Field(default=0, ge=0, le=10_000)
    question_types: list[str] = Field(default_factory=lambda: list(DEFAULT_QUESTION_TYPES), min_length=1, max_length=20)

    @field_validator("question_types")
    @classmethod
    def _normalize_question_types(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for question_type in value:
            item = question_type.strip()
            if not item:
                continue
            if len(item) > 80:
                raise ValueError("题型名称不能超过 80 个字符")
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("知识点至少需要启用一种题型")
        return normalized


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
