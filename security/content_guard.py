# security/content_guard.py
"""
内容安全审核模块 - 基于 any-guardrail 统一接口
"""
import os
from typing import Optional, List
from any_guardrail import AnyGuardrail, GuardrailName, GuardrailOutput


class ContentGuard:
    """内容安全护栏"""

    def __init__(self, provider: str = "DEEPSET"):
        """
        初始化内容审核器

        支持的 provider:
        - DEEPSET: 轻量级，无需GPU[reference:2]
        - LLAMA_GUARD: 需要HuggingFace权限和GPU
        - SHIELDGEMMA: Google的安全护栏模型
        """
        self.provider_name = provider
        try:
            self.guardrail = AnyGuardrail.create(
                getattr(GuardrailName, provider)
            )
        except Exception as e:
            print(f"⚠️ 初始化 {provider} 失败: {e}")
            # 降级到 DEEPSET
            self.guardrail = AnyGuardrail.create(GuardrailName.DEEPSET)

    def validate_input(self, text: str) -> tuple[bool, Optional[str], float]:
        """
        审核用户输入
        返回: (是否安全, 原因, 风险分数)
        """
        if not text or not text.strip():
            return True, None, 0.0

        result: GuardrailOutput = self.guardrail.validate(text)

        # GuardrailOutput 结构:
        # - result.valid: bool 是否通过
        # - result.score: float 风险分数 0-1
        # - result.categories: 各分类检测结果
        # - result.explanation: 人类可读的解释

        if not result.valid:
            flagged = [c.name for c in result.categories if c.triggered]
            reason = f"内容安全检测不通过: {', '.join(flagged)}"
            if result.explanation:
                reason += f" - {result.explanation}"
            return False, reason, result.score or 0.8

        return True, None, result.score or 0.0

    def validate_output(self, text: str) -> tuple[bool, Optional[str], float]:
        """审核模型输出"""
        return self.validate_input(text)


# 单例实例
_content_guard: Optional[ContentGuard] = None


def get_content_guard() -> ContentGuard:
    global _content_guard
    if _content_guard is None:
        provider = os.getenv("CONTENT_SAFETY_PROVIDER", "DEEPSET")
        _content_guard = ContentGuard(provider)
    return _content_guard