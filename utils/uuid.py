import secrets
from typing import Optional


def create_agent_id(label: Optional[str] = None) -> str:
    """ 生成带前缀的 agent id，格式用于与任务 ID 保持一致。

    Args:
        label: 可选标签字符串，若提供则作为前缀的一部分并以连字符分隔。

    Returns:
        str: 生成的 agent id，格式为 "{label}-随机16字符十六进制字符串"。

    """
    suffix = secrets.token_hex(8)

    if label:
        return f"{label}-{suffix}"
    return f"agent-{suffix}"