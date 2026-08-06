# security/__init__.py
import os
from .content_guard import get_content_guard
from .prompt_guard import get_prompt_guard
from .tool_guard import get_tool_guard
from .audit import get_audit_logger

def init_security():
    """初始化所有安全模块"""
    config = {
        "content_safety": os.getenv("CONTENT_SAFETY_ENABLED", "true").lower() == "true",
        "prompt_injection": os.getenv("PROMPT_INJECTION_ENABLED", "true").lower() == "true",
        "tool_guard": os.getenv("TOOL_GUARD_ENABLED", "true").lower() == "true",
        "audit_log": os.getenv("AUDIT_LOG_ENABLED", "true").lower() == "true",
    }
    if config["content_safety"]:
        get_content_guard()
        print("✅ 内容安全模块已初始化")
    if config["prompt_injection"]:
        get_prompt_guard()
        print("✅ 提示词安全模块已初始化")
    if config["tool_guard"]:
        get_tool_guard()
        print("✅ 工具护栏模块已初始化")
    if config["audit_log"]:
        get_audit_logger()
        print("✅ 审计日志模块已初始化")
    return config