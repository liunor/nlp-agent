"""Prompt specifications and version-aware Markdown template registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.prompt_runtime.cache import PromptTemplateCache
from core.prompt_runtime.validator import PromptValidationError, validate_template


@dataclass(frozen=True, slots=True)
class PromptSpec:
    id: str
    version: str
    template: str
    variables: frozenset[str] = frozenset()
    description: str = ""


DEFAULT_SPECS: tuple[PromptSpec, ...] = (
    PromptSpec("coordinator", "1.0", "coordinator.md", frozenset({"worker_profiles"})),
    PromptSpec("worker", "1.0", "worker.md", frozenset({"today"})),
    PromptSpec("runtime.exhaustion", "1.0", "runtime/exhaustion.md", frozenset({"reason"})),
    PromptSpec("retry.empty_response", "1.0", "retry/empty_response.md"),
    PromptSpec("retry.continue_after_truncation", "1.0", "retry/continue_after_truncation.md"),
    PromptSpec("memory.inject", "1.0", "memory/inject.md", frozenset({"memory"})),
    PromptSpec("memory.curator", "1.0", "memory/curator.md"),
    PromptSpec("memory.curate_request", "1.0", "memory/curate_request.md", frozenset({"memory", "archives"})),
    PromptSpec("compression.auto_summary", "1.0", "compression/auto_summary.md", frozenset({"conversation"})),
    PromptSpec("compression.collapse_summary", "1.0", "compression/collapse_summary.md", frozenset({"conversation"})),
    PromptSpec("session.summary", "1.0", "session/session_summary.md", frozenset({"conversation"})),
    PromptSpec("tool.contract", "1.0", "tool/tool_contract.md"),
    PromptSpec(
        "learning.policy",
        "1.0",
        "learning/policy.md",
        frozenset({"topic_policy", "progress_policy"}),
    ),
    PromptSpec("learning.topic", "1.0", "learning/topic.md", frozenset({"topic_name", "topic_description", "knowledge_points"})),
    PromptSpec("learning.level.beginner", "1.0", "learning/levels/beginner.md"),
    PromptSpec("learning.level.intermediate", "1.0", "learning/levels/intermediate.md"),
    PromptSpec("learning.level.advanced", "1.0", "learning/levels/advanced.md"),
    PromptSpec("learning.mode.explain", "1.0", "learning/modes/explain.md"),
    PromptSpec("learning.mode.socratic", "1.0", "learning/modes/socratic.md", frozenset({"guided_session", "guided_blueprint"})),
    PromptSpec("learning.mode.practice", "1.0", "learning/modes/practice.md", frozenset({"exercise_session", "exercise_blueprint"})),
    PromptSpec("learning.mode.review", "1.0", "learning/modes/review.md", frozenset({"review_blueprint", "exercise_session"})),
)


class PromptRegistry:
    """Loads registered Markdown prompts; a version may be overridden per id."""

    def __init__(self, templates_dir: str | Path | None = None, *, versions: dict[str, str] | None = None) -> None:
        self.templates_dir = Path(templates_dir) if templates_dir else Path(__file__).parent / "templates"
        self.versions = dict(versions or {})
        self.cache = PromptTemplateCache()
        self._specs: dict[str, PromptSpec] = {}
        for spec in DEFAULT_SPECS:
            self.register(spec)

    def register(self, spec: PromptSpec) -> None:
        if spec.id in self._specs:
            raise PromptValidationError(f"duplicate Prompt id: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, prompt_id: str) -> PromptSpec:
        try:
            spec = self._specs[prompt_id]
        except KeyError as error:
            raise PromptValidationError(f"unknown Prompt id: {prompt_id}") from error
        selected_version = self.versions.get(prompt_id, spec.version)
        if selected_version == spec.version:
            return spec
        suffix = Path(spec.template).suffix
        versioned = Path(spec.template).with_suffix("").as_posix() + f".v{selected_version}{suffix}"
        return PromptSpec(spec.id, selected_version, versioned, spec.variables, spec.description)

    def load(self, prompt_id: str) -> tuple[PromptSpec, str]:
        spec = self.get(prompt_id)
        path = self.templates_dir / spec.template
        if not path.is_file():
            raise PromptValidationError(f"Prompt {prompt_id!r} template not found: {path}")
        template = self.cache.read(path)
        validate_template(prompt_id=prompt_id, template=template, variables=spec.variables)
        return spec, template

    def reload(self) -> None:
        self.cache.clear()
