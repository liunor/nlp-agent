from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from core.model_runtime.factory import get_global_model_factory


def _parse_json(text: str) -> dict:
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge returned no JSON object")
    value = json.loads(candidate[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("judge result is not an object")
    return value


async def judge_case(model, *, blueprint: dict, outcome: dict) -> dict:
    turns = outcome["snapshot"]["turns"]
    question_turn, grading_turn = turns[0], turns[1]
    system = SystemMessage(content=(
        "你是教育测评 Judge 模型。请只根据给定蓝图、学生答案和 Agent 评分反馈进行独立评审，"
        "不要因为架构 verdict PASS 就默认内容正确。严格输出 JSON，不要 Markdown："
        '{"question_quality":0,"student_answer_quality":0,"rubric_coverage":0,"grading_correctness":0,"feedback_quality":0,"overall":0,"issues":[],"rationale":""}。'
        "所有分数为 0-2：0=不合格，1=部分合格，2=合格。"
        "question_quality 检查是否只出一道题且符合 instructions、question_type 和知识范围；"
        "student_answer_quality 检查学生答案是否覆盖 rubric；rubric_coverage 检查反馈是否逐项处理 rubric；"
        "grading_correctness 检查 achieved 判断是否与学生答案和 rubric 证据一致；"
        "feedback_quality 检查是否给出具体、准确、可学习的反馈和正确答案要点；overall 是综合内容质量。"
    ))
    prompt = {
        "blueprint": blueprint,
        "question": question_turn.get("question", ""),
        "student_answer": grading_turn.get("student_reply", ""),
        "agent_feedback": grading_turn.get("agent_reply", ""),
        "architecture_metrics": outcome.get("architecture", {}).get("metrics", {}),
    }
    from core.model_runtime.usage import system_usage_attribution

    with system_usage_attribution(purpose="evaluation"):
        response = await model.ainvoke([system, HumanMessage(content=json.dumps(prompt, ensure_ascii=False))])
    raw = response.content if isinstance(response.content, str) else ""
    try:
        result = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as error:
        result = {"question_quality": 0, "student_answer_quality": 0, "rubric_coverage": 0, "grading_correctness": 0, "feedback_quality": 0, "overall": 0, "issues": [f"judge_parse_error: {error}"], "rationale": raw[:2_000]}
    result["case_id"] = outcome["case_id"]
    return result


async def run(report_path: Path, output: Path | None = None) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    blueprint_path = report_path.parents[2] / ".." / "suites" / "exercise-blueprint-multiturn-v1" / "blueprint.yaml"
    import yaml
    blueprint = yaml.safe_load(blueprint_path.resolve().read_text(encoding="utf-8"))
    model = get_global_model_factory().build_preset("utility-flash")
    results = []
    for outcome in report["outcomes"]:
        results.append(await judge_case(model, blueprint=blueprint, outcome=outcome))
    valid = [item for item in results if isinstance(item.get("overall"), (int, float))]
    summary = {
        "judge_model": "utility-flash",
        "source_report": str(report_path),
        "created_at": datetime.now().astimezone().isoformat(),
        "cases": len(results),
        "average_overall": round(sum(float(item.get("overall", 0)) for item in valid) / len(valid), 2) if valid else 0,
        "content_pass_rate": round(sum(float(item.get("overall", 0)) >= 2 for item in valid) / len(valid), 2) if valid else 0,
        "results": results,
    }
    target = output or report_path.with_name(report_path.stem + ".judge.json")
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved judge report: {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.report))


if __name__ == "__main__":
    main()
