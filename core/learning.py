"""Structured learning state shared by Web adapters, Gateway, and Coordinator."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def knowledge_point_ids(blueprint: dict[str, Any]) -> list[str]:
    """Resolve knowledge-point refs from both legacy and current blueprint shapes."""
    single = blueprint.get("knowledge_point_id")
    if single:
        return [str(single)]
    many = blueprint.get("knowledge_point_ids")
    if isinstance(many, list):
        return [str(item) for item in many if item]
    return []


class LearningContext(BaseModel):
    """The learner-selected policy for one turn; never encoded into user text."""

    model_config = ConfigDict(extra="forbid")

    topic_id: str | None = Field(default=None, min_length=1, max_length=64)
    topic_name: str = Field(default="", max_length=120)
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    mode: Literal["explain", "socratic", "practice", "review"] = "explain"

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_topic(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy_topic = data.pop("topic", None)
        if legacy_topic and not data.get("topic_name"):
            data["topic_name"] = legacy_topic
        return data

    @property
    def topic(self) -> str:
        """Compatibility view for legacy prompt and transcript callers."""
        return self.topic_name


class LearningProgress(BaseModel):
    """Small session-scoped state that survives context compaction."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(default="", max_length=500)
    stage: str = Field(default="start", max_length=120)
    known_concepts: list[str] = Field(default_factory=list, max_length=30)
    misconceptions: list[str] = Field(default_factory=list, max_length=20)
    last_question: str = Field(default="", max_length=2_000)
    attempts: int = Field(default=0, ge=0, le=1000)


class ExerciseState(BaseModel):
    """The active practice/review item.  A blueprint stays teacher-owned."""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str | None = Field(default=None, max_length=128)
    exercise_session_id: str | None = Field(default=None, max_length=128)
    question: str = Field(default="", max_length=8_000)
    rubric: list[str] = Field(default_factory=list, max_length=20)
    question_number: int = Field(default=0, ge=0, le=1000)
    # New sessions always write 1; the wider bound keeps historical multi-question turn snapshots readable.
    question_count: int = Field(default=1, ge=1, le=1000)
    attempt: int = Field(default=0, ge=0, le=20)
    status: Literal["idle", "awaiting_answer", "reviewing", "completed"] = "idle"


class TeachingMaterials(BaseModel):
    """Server-owned prompt material resolved from the teacher catalogue."""

    model_config = ConfigDict(extra="forbid")

    learning_topic: dict[str, Any] = Field(default_factory=dict)
    exercise_blueprint: dict[str, Any] = Field(default_factory=dict)
    review_blueprint: dict[str, Any] = Field(default_factory=dict)
    guided_session: dict[str, Any] = Field(default_factory=dict)
    guided_blueprint: dict[str, Any] = Field(default_factory=dict)


def default_progress(context: LearningContext) -> LearningProgress:
    return LearningProgress(objective=f"学习并掌握{context.topic_name}" if context.topic_id else "")
