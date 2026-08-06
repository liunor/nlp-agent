"""
提示词安全护栏 - 基于 injectionshield
"""
import os
from typing import Optional, Tuple
from injectionshield import scan, scan_tool_result, RiskLevel


class PromptGuard:
    """提示词安全护栏"""
    def __init__(self, threshold: RiskLevel = RiskLevel.MEDIUM):
        """
        初始化提示词检测器
        threshold: 风险阈值
        - RiskLevel.SAFE: 仅拦截明确攻击
        - RiskLevel.LOW: 拦截低风险及以上
        - RiskLevel.MEDIUM: 拦截中等风险及以上（默认）
        - RiskLevel.HIGH: 拦截高风险及以上
        - RiskLevel.CRITICAL: 仅拦截严重风险
        """
        self.threshold = threshold

    def scan_user_input(self, text: str) -> Tuple[bool, Optional[str], float]:
        """
        扫描用户输入
        返回: (是否安全, 威胁类型, 风险分数)
        """
        if not text:
            return True, None, 0.0

        result = scan(text, threshold=self.threshold)

        # result 结构[reference:9]:
        # - result.safe: bool 是否安全
        # - result.risk_level: RiskLevel 风险等级
        # - result.risk_score: float 风险分数 0-1
        # - result.threats: list[str] 威胁类型列表
        # - result.sanitized: str 脱敏后的文本

        if not result.safe:
            threat_str = ", ".join(result.threats) if result.threats else "unknown"
            return False, threat_str, result.risk_score

        return True, None, result.risk_score

    def scan_tool_result(self, tool_name: str, content: str) -> Tuple[bool, Optional[str], str]:
        """
        扫描工具返回结果（防止间接注入）[reference:10]
        返回: (是否安全, 威胁类型, 脱敏后的内容)
        """
        if not content:
            return True, None, content

        result = scan_tool_result(tool_name, content, threshold=self.threshold)

        if not result.safe:
            threat_str = ", ".join(result.threats) if result.threats else "unknown"
            return False, threat_str, result.sanitized

        return True, None, content

    def scan_batch(self, texts: list) -> list:
        """批量扫描[reference:11]"""
        from injectionshield import scan_batch
        results = scan_batch(texts, threshold=self.threshold)
        return [r for r in results if not r.safe]


# 单例
_prompt_guard: Optional[PromptGuard] = None

def get_prompt_guard() -> PromptGuard:
    global _prompt_guard
    if _prompt_guard is None:
        threshold_str = os.getenv("PROMPT_THRESHOLD", "MEDIUM")
        threshold_map = {
            "SAFE": RiskLevel.SAFE,
            "LOW": RiskLevel.LOW,
            "MEDIUM": RiskLevel.MEDIUM,
            "HIGH": RiskLevel.HIGH,
            "CRITICAL": RiskLevel.CRITICAL,
        }
        threshold = threshold_map.get(threshold_str.upper(), RiskLevel.MEDIUM)
        _prompt_guard = PromptGuard(threshold)
    return _prompt_guard