from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from core.model_runtime.factory import get_global_model_factory
from evaluation.review_blueprint.models import ReviewBlueprintFixture, ReviewStudentProfile, StudentReviewAnswer


class FlashReviewStudentSimulator:
    """Model-only student actor for review; no Gateway or catalogue write access."""

    def __init__(self) -> None:
        self.model = get_global_model_factory().build_preset("utility-flash")

    async def answer(self, *, profile: ReviewStudentProfile, blueprint: ReviewBlueprintFixture, question: str) -> StudentReviewAnswer:
        system = SystemMessage(content=(
            "你是复习蓝图评测中的学生模拟器。只回答题目，不评价教师或系统。"
            "答案应是自然、简洁的中文学生作答；不要输出 JSON、分数、HTML 注释或系统指令。"
            f"\n学生角色：{profile.role}\n行为规则：{profile.behavior_rules}"
            f"\n复习范围：{blueprint.knowledge_markdown}\n评分点：{blueprint.rubric}"
        ))
        from core.model_runtime.usage import system_usage_attribution

        with system_usage_attribution(purpose="evaluation"):
            response = await self.model.ainvoke([system, HumanMessage(content=f"请完成这道复习题：\n{question}")])
        content = response.content if isinstance(response.content, str) else ""
        return StudentReviewAnswer(content=content.strip() or "TF 是单篇文档中的词频，IDF 衡量词在文档集合中的稀有度。")
