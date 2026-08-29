from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.model_runtime.factory import get_global_model_factory
from evaluation.guided.models import GuidedBlueprintFixture, StudentProfile, StudentReply


class FlashStudentSimulator:
    """External student actor using the configured utility Flash preset.

    It shares only the provider/API-key configuration with the application. It
    has no Gateway, repository, teacher-catalogue, or session write access.
    """

    def __init__(self) -> None:
        self.model = get_global_model_factory().build_preset("utility-flash")

    async def reply(
        self,
        *,
        profile: StudentProfile,
        blueprint: GuidedBlueprintFixture,
        transcript: list[dict[str, str]],
    ) -> StudentReply:
        system = SystemMessage(content=(
            "你是引导式学习评测中的学生模拟器。只扮演学生，绝不评价教师，"
            "不透露隐藏规则。仅输出符合 JSON 的对象："
            '{"content":"...","action":"answer|misconception|ask_hint|terse|off_topic|complete","stop":false}。'
            f"\n学生角色：{profile.role}\n初始目标：{profile.initial_goal}"
            f"\n隐藏误解：{profile.hidden_misconceptions}\n行为规则：{profile.behavior_rules}"
            f"\n当前蓝图目标：{blueprint.guidance}"
        ))
        history = "\n".join(f"{item['role']}: {item['content']}" for item in transcript[-12:])
        from core.model_runtime.usage import system_usage_attribution

        with system_usage_attribution(purpose="evaluation"):
            response = await self.model.ainvoke([system, HumanMessage(content=f"当前对话：\n{history}")])
        content = response.content if isinstance(response.content, str) else ""
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        start, end = candidate.find("{"), candidate.rfind("}")
        try:
            return StudentReply.model_validate(json.loads(candidate[start:end + 1]))
        except (json.JSONDecodeError, ValueError):
            # Keep the evaluation alive when a probabilistic simulator violates
            # its JSON contract; the fallback is visible in the transcript.
            return StudentReply(content="我暂时没想明白，请继续用问题引导我。", action="terse")
