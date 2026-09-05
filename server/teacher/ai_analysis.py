"""DeepSeek enhancement for the deterministic teacher learning analysis.

The statistics in this module are never calculated by the model.  The model
receives a small, privacy-safe projection of the backend result and its
response is validated back against that result before it can be shown.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from server.agent.llm_factory import get_utility_llm

logger = logging.getLogger(__name__)

AI_ERROR_TYPES = ("概念理解不足", "方法不熟", "计算错误", "前置知识不足", "数据不足，暂不判断")


class _ModelDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_point_id: str | None = Field(default=None, max_length=128)
    knowledge_point: str | None = Field(default=None, max_length=160)
    level: Literal["high", "medium", "low"] = "medium"
    problem: str = Field(min_length=1, max_length=1_000)
    cause: str | None = Field(default=None, max_length=1_000)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    suggestions: list[str] = Field(default_factory=list, max_length=3)
    confidence: Literal["high", "medium", "low"] = "medium"
    data_gaps: list[str] = Field(default_factory=list, max_length=8)
    error_type: str | None = Field(default=None, max_length=80)


class _ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    diagnoses: list[_ModelDiagnosis] = Field(default_factory=list, max_length=10)


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class LearningAnalysisAICache:
    """Small process-local cache; intentionally no database table in v1.

    The cache is scoped to a single process: it keeps the AI report stable
    across tab switches within the TTL for the single-replica deployment.
    Multi-replica deployments must move this behind a shared store (Redis or the
    application database) so replicas do not independently re-invoke DeepSeek.
    Entries are bounded to ``max_items`` with FIFO eviction so a long-running
    process cannot grow the dict without limit.
    """

    def __init__(self, ttl_seconds: int = 1_800, max_items: int = 128) -> None:
        self.ttl_seconds = max(1, ttl_seconds)
        self.max_items = max(1, max_items)
        self._items: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return dict(entry.value)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = _CacheEntry(dict(value), time.monotonic() + self.ttl_seconds)
        while len(self._items) > self.max_items:
            self._items.pop(next(iter(self._items)), None)

    def clear(self) -> None:
        self._items.clear()


learning_analysis_ai_cache = LearningAnalysisAICache()


def _number(value: Any) -> str:
    if value is None:
        return "暂无"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "暂无"


def _backend_evidence(item: dict[str, Any]) -> list[str]:
    return [
        (
            f"掌握率 {_number(item.get('mastery_rate'))}%；"
            f"上期掌握率 {_number(item.get('previous_mastery_rate'))}%；"
            f"错误 {int(item.get('error_count') or 0)} 次；"
            f"重复错误学生 {int(item.get('repeated_error_student_count') or 0)} 人；"
            f"题目 {int(item.get('question_count') or 0)} 道；"
            f"涉及学生 {int(item.get('student_count') or 0)} 人；"
            f"作答 {int(item.get('attempt_count') or 0)} 次。"
        )
    ]


# 单一映射表：确定性“错误模式”(problem_type) → 模型“认知原因”(error_type)。
# 只有语义可严格对应的才转换；“易错点集中”“练习覆盖不足”“学习参与不足”
# 描述的是错误／覆盖／参与模式，规则侧无法可靠推断具体认知原因，因此规则兜底时
# 保留原始 problem_type 标签，交由模型在给定候选词表内自行判断（见 _prompt）。
_PROBLEM_TYPE_TO_ERROR_TYPE = {
    "概念掌握不足": "概念理解不足",
    "解题方法不熟": "方法不熟",
}


def _fallback_error_type(item: dict[str, Any]) -> str:
    problem_type = str(item.get("problem_type") or "")
    if item.get("data_sufficiency") == "insufficient" or problem_type == "数据不足，暂不判断":
        return "数据不足，暂不判断"
    return _PROBLEM_TYPE_TO_ERROR_TYPE.get(problem_type) or problem_type or "方法不熟"


def _matches_filter(item: dict[str, Any], value: str, *, id_key: str, name_key: str) -> bool:
    return value in {"", "all"} or value == str(item.get(id_key) or "") or value == str(item.get(name_key) or "")


def build_ai_analysis_material(
    overview: dict[str, Any],
    *,
    course_id: str = "all",
    content_scope: str = "all",
    limit: int = 5,
) -> dict[str, Any]:
    """Project the top deterministic diagnoses into a privacy-safe AI payload."""
    analysis = overview.get("learning_analysis") or {}
    scope = analysis.get("scope") or {}
    diagnoses = [
        item
        for item in analysis.get("diagnoses", [])
        if isinstance(item, dict)
        and item.get("data_sufficiency") == "sufficient"
        and _matches_filter(item, course_id, id_key="content_id", name_key="content_name")
        and _matches_filter(item, content_scope, id_key="knowledge_point_id", name_key="knowledge_point_name")
    ]
    contents: list[dict[str, Any]] = []
    for item in diagnoses[: max(1, limit)]:
        examples = [
            {
                "question_id": str(example.get("question_id") or ""),
                "question": str(example.get("question") or "")[:500],
                "score": example.get("score"),
                "passed": bool(example.get("passed")),
            }
            for example in item.get("question_examples", [])[:3]
            if isinstance(example, dict) and example.get("question")
        ]
        contents.append(
            {
                "content_id": str(item.get("content_id") or ""),
                "content_name": str(item.get("content_name") or ""),
                "knowledge_point_id": str(item.get("knowledge_point_id") or ""),
                "knowledge_point": str(item.get("knowledge_point_name") or ""),
                "question_count": int(item.get("question_count") or 0),
                "student_count": int(item.get("student_count") or 0),
                "attempt_count": int(item.get("attempt_count") or 0),
                "mastery_rate": None if item.get("mastery_rate") is None else round(float(item["mastery_rate"]) / 100, 4),
                "previous_mastery_rate": None if item.get("previous_mastery_rate") is None else round(float(item["previous_mastery_rate"]) / 100, 4),
                "error_count": int(item.get("error_count") or 0),
                "repeated_error_students": int(item.get("repeated_error_student_count") or 0),
                "trend": {"down": "下降", "up": "上升", "stable": "稳定"}.get(str(item.get("trend")), "稳定"),
                "problem_type": str(item.get("problem_type") or "数据不足，暂不判断"),
                "error_type": _fallback_error_type(item),
                "data_sufficiency": str(item.get("data_sufficiency") or "insufficient"),
                "weak_criteria": item.get("weak_criteria") or [],
                "question_examples": examples,
                "rule_problem": str((item.get("recommendation") or {}).get("conclusion") or "当前学习表现需要继续观察。"),
                "rule_suggestion": str((item.get("recommendation") or {}).get("action") or "先补充练习并继续观察下一周期表现。"),
            }
        )
    return {
        "course": str((overview.get("goals") or {}).get("course_title") or "当前课程"),
        "period": str(scope.get("period_label") or "当前分析周期"),
        "student_role": "学生",
        "contents": contents,
    }


def _decode_model_content(response: Any) -> str:
    if isinstance(response, AIMessage):
        content = response.content
    elif isinstance(response, dict):
        content = response.get("content") or response.get("text") or ""
    else:
        content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
    return str(content).strip()


def _parse_json_response(response: Any) -> dict[str, Any]:
    content = _decode_model_content(response)
    if content.startswith("```"):
        content = content.strip("`")
        if content.lstrip().startswith("json"):
            content = content.lstrip()[4:].lstrip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的 JSON 根节点必须是对象")
    return parsed


def validate_ai_analysis_response(raw: dict[str, Any], source_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate model shape and bind every visible diagnosis to backend data."""
    try:
        response = _ModelResponse.model_validate(raw)
    except ValidationError as error:
        raise ValueError("模型返回格式不符合学习分析协议") from error
    by_id = {str(item.get("knowledge_point_id")): item for item in source_items}
    by_name = {str(item.get("knowledge_point_name")): item for item in source_items}
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in response.diagnoses:
        source = by_id.get(str(item.knowledge_point_id or "")) or by_name.get(str(item.knowledge_point or ""))
        if source is None or source.get("data_sufficiency") != "sufficient":
            continue
        suggestions = [suggestion.strip() for suggestion in item.suggestions[:3] if suggestion.strip()]
        if not item.evidence or not suggestions:
            continue
        point_id = str(source.get("knowledge_point_id"))
        if point_id in seen:
            continue
        seen.add(point_id)
        error_type = item.error_type if item.error_type in AI_ERROR_TYPES else _fallback_error_type(source)
        visible.append(
            {
                "knowledge_point_id": point_id,
                "knowledge_point_name": str(source.get("knowledge_point_name") or ""),
                "level": item.level,
                "problem": item.problem.strip(),
                "cause": (item.cause or item.problem).strip(),
                "evidence": _backend_evidence(source),
                "suggestions": suggestions,
                "confidence": item.confidence,
                "data_gaps": [gap.strip() for gap in item.data_gaps if gap.strip()],
                "error_type": error_type,
                "question_examples": source.get("question_examples") or [],
            }
        )
    if not visible:
        raise ValueError("模型没有返回可绑定后端证据的诊断")
    return {"summary": response.summary.strip(), "diagnoses": visible}


def _rule_result(material: dict[str, Any], message: str) -> dict[str, Any]:
    diagnoses = []
    for item in material.get("contents", []):
        if item.get("data_sufficiency") != "sufficient":
            continue
        point_name = str(item.get("knowledge_point") or item.get("knowledge_point_name") or "该知识点")
        rate = item.get("mastery_rate")
        previous = item.get("previous_mastery_rate")
        level = "high" if item.get("problem_type") in {"概念掌握不足", "易错点集中"} or (rate is not None and rate < 0.6) else "medium"
        diagnoses.append(
            {
                "knowledge_point_id": item["knowledge_point_id"],
                "knowledge_point_name": point_name,
                "level": level,
                "problem": str(item.get("rule_problem") or item.get("problem_type") or "当前学习表现需要继续观察。"),
                "cause": str(item.get("rule_problem") or item.get("problem_type") or "当前学习表现需要继续观察。"),
                "evidence": [
                    (
                        f"掌握率 {_number(rate * 100 if rate is not None else None)}%；"
                        f"上期掌握率 {_number(previous * 100 if previous is not None else None)}%；"
                        f"错误 {int(item.get('error_count') or 0)} 次；"
                        f"重复错误学生 {int(item.get('repeated_error_students') or 0)} 人。"
                    )
                ],
                "suggestions": [str(item.get("rule_suggestion") or "先补充练习并继续观察下一周期表现。")],
                "confidence": "medium",
                "data_gaps": [],
                "error_type": str(item.get("error_type") or _fallback_error_type(item)),
                "question_examples": item.get("question_examples") or [],
            }
        )
    if not diagnoses:
        return {
            "status": "failed",
            "source": "rules",
            "message": f"{message} 当前样本不足，已展示规则版诊断。",
            "summary": "当前筛选范围内暂无足够的学生练习证据，暂不生成确定性 AI 判断。",
            "diagnoses": [],
        }
    names = "、".join(item["knowledge_point_name"] for item in diagnoses[:3])
    return {
        "status": "failed",
        "source": "rules",
        "message": f"{message} 已展示规则版诊断。",
        "summary": f"本周期需要优先关注的内容包括“{names}”。以下结论基于后端统计规则生成，教师可结合课堂情况判断。",
        "diagnoses": diagnoses[:5],
    }


def _prompt(material: dict[str, Any]) -> str:
    error_vocabulary = "、".join(AI_ERROR_TYPES)
    return (
        "你是教学数据分析助手。只分析以下聚合后的学生学习数据，不能索引学生身份，也不能自行计算或修改任何统计数字。\n"
        "请严格返回 JSON，不要 Markdown，不要解释 JSON 之外的内容。\n"
        "只返回前 5 个最需要关注且 data_sufficiency=sufficient 的知识点；每个知识点最多 3 条 suggestions。"
        "evidence 必须非空，problem 和 suggestions 必须与 evidence 对应。\n"
        f"模型只负责解释共性问题，error_type 必须从给定候选词表中选择其一：{error_vocabulary}，并给教师参考动作。"
        "不要生成完整教案，不要排名，不要调整教学计划，不要发布练习。\n"
        "返回结构：{summary:string, diagnoses:[{knowledge_point_id:string,level:high|medium|low,problem:string,cause:string,evidence:string[],suggestions:string[],confidence:high|medium|low,data_gaps:string[],error_type:string}]}\n"
        f"数据：{json.dumps(material, ensure_ascii=False, separators=(',', ':'))}"
    )


async def generate_ai_analysis(material: dict[str, Any], *, model: Any | None = None) -> dict[str, Any]:
    """Call the configured DeepSeek utility interface, falling back to rules."""
    try:
        if not any(item.get("data_sufficiency") == "sufficient" for item in material.get("contents", [])):
            return _rule_result(material, "当前筛选范围")
        llm = model or get_utility_llm(model_profile="deepseek")
        parsed = _parse_json_response(await llm.ainvoke([SystemMessage(content="你必须遵守结构化 JSON 输出协议。"), HumanMessage(content=_prompt(material))]))
        validated = validate_ai_analysis_response(parsed, material.get("contents", []))
        return {
            "status": "completed",
            "source": "deepseek",
            "message": "",
            "model_id": str(getattr(llm, "model_name", "deepseek-v4-flash")),
            **validated,
        }
    except (Exception, ValueError) as error:
        logger.warning("teacher learning analysis AI fallback: %s", error)
        return _rule_result(material, "DeepSeek 暂时不可用或返回格式异常")
