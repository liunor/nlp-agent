from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    ordered_tools: list[str] = Field(default_factory=list)
    allowed_tool_statuses: list[str] = Field(default_factory=lambda: ["ok"])
    allow_extra_tools: bool = False
    expected_no_tool: bool = False
    delegation_policy: Literal["required", "preferred", "optional", "forbidden"] = "required"
    min_workers: int = Field(default=0, ge=0)
    max_workers: int | None = Field(default=None, ge=0)
    worker_required_tools: list[str] = Field(default_factory=list)
    min_dispatches: int = Field(default=0, ge=0)
    max_dispatches: int | None = Field(default=None, ge=0)
    require_join: bool = False
    required_wait_mode: Literal["all", "any", "quorum"] | None = None
    orchestration_mode: Literal["parallel", "sequential"] | None = None
    final_response_terms: list[str] = Field(default_factory=list)
    require_citation_integrity: bool = False


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    input: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    expectation: Expectation


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    suite: dict[str, str]
    defaults: dict = Field(default_factory=dict)
    cases: list[EvaluationCase] = Field(min_length=1)


class ToolCallEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    turn_id: str
    tool_name: str
    sequence: int
    status: str
    attempts: int = 1
    argument_keys: tuple[str, ...] = ()
    duration_ms: int = 0
    worker_id: str | None = None
    result_urls: tuple[str, ...] = ()


class WorkerEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    worker_id: str
    attempt: int = 1
    status: str
    duration_ms: int = 0
    tool_names: tuple[str, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DispatchEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    worker_id: str
    agent_name: str
    join: bool
    wait_mode: str
    quorum: int
    directive_chars: int


class TurnEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str | None = None
    turn_id: str
    trace_status: str | None = None
    calls: tuple[ToolCallEvidence, ...] = ()
    workers: tuple[WorkerEvidence, ...] = ()
    worker_attempts: tuple[WorkerEvidence, ...] = ()
    dispatches: tuple[DispatchEvidence, ...] = ()


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    verdict: Literal["PASS", "WARN", "FAIL", "BLOCKED", "CRITICAL_FAIL"]
    score: float
    hard_failures: tuple[str, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    trace_id: str | None = None
    tool_calls: tuple[ToolCallEvidence, ...] = ()
    final_text: str | None = None
    completed_at: datetime | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    suite_id: str
    dataset_sha256: str
    started_at: datetime
    completed_at: datetime
    results: tuple[CaseResult, ...]
    metrics: dict[str, float]
    verdict: Literal["PASS", "FAIL", "BLOCKED"]
