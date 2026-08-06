"""
工具调用护栏 - 基于 Doberman 架构
"""
import os
import json
import hashlib
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime


class Verdict(Enum):
    """Doberman 三种决策"""
    PASS = "pass"  # 允许执行
    AUTH = "auth"  # 需要人工审批
    BLOCK = "block"  # 阻止执行


@dataclass
class ToolCallRecord:
    """工具调用记录"""
    tool_name: str
    args: Dict[str, Any]
    verdict: Verdict
    reason: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class ToolGuard:
    """工具调用安全护栏"""

    def __init__(self):
        # 工具白名单
        self._allowlist = {
            "search_docs",
            "get_learning_path",
            "evaluate_answer",
            "get_progress",
            "web_search",
        }

        # 工具黑名单 - 直接阻止
        self._denylist = {
            "execute_code",
            "run_shell",
            "delete_file",
            "modify_system",
            "access_network",
            "rm",  # 防止 rm -rf /
            "subprocess_call",
            "eval",
            "exec",
        }

        # 高风险工具 - 需要审批
        self._auth_required = {
            "send_email",
            "create_user",
            "update_settings",
            "write_file",
            "delete_file",
            "git_push_force",
        }

        # 敏感路径模式
        self._sensitive_paths = [
            "/etc/",
            "/root/",
            "~/.ssh/",
            ".env",
            "secrets",
            "credentials",
            "password",
        ]

        # 待审批队列
        self._pending_approvals: Dict[str, Dict] = {}

    def evaluate(self, tool_name: str, tool_args: Dict[str, Any]) -> tuple[Verdict, str]:
        """
        评估工具调用
        返回: (决策, 原因)
        """
        # 1. 黑名单检查
        if tool_name in self._denylist:
            return Verdict.BLOCK, f"工具 '{tool_name}' 被禁止使用"

        # 2. 检查敏感参数
        if self._has_sensitive_args(tool_args):
            return Verdict.BLOCK, "检测到敏感参数（密码/密钥/凭证）"

        # 3. 检查敏感路径
        if self._has_sensitive_path(tool_args):
            return Verdict.AUTH, "目标路径包含敏感内容，需要审批"

        # 4. 白名单检查
        if tool_name in self._allowlist:
            return Verdict.PASS, "通过"

        # 5. 需要审批的工具
        if tool_name in self._auth_required:
            return Verdict.AUTH, f"工具 '{tool_name}' 需要人工审批"

        # 6. 未知工具 - 默认阻止（安全优先）
        return Verdict.BLOCK, f"未知工具 '{tool_name}'，默认阻止"

    def _has_sensitive_args(self, args: Dict) -> bool:
        """检查参数是否包含敏感内容"""
        sensitive_patterns = ["password", "secret", "token", "key", "credential", "api_key"]
        for key, value in args.items():
            key_lower = key.lower()
            if any(p in key_lower for p in sensitive_patterns):
                return True
            if isinstance(value, str):
                if any(p in value.lower() for p in sensitive_patterns):
                    return True
        return False

    def _has_sensitive_path(self, args: Dict) -> bool:
        """检查参数是否包含敏感路径"""
        for value in args.values():
            if isinstance(value, str):
                for pattern in self._sensitive_paths:
                    if pattern in value:
                        return True
        return False

    def request_approval(self, tool_name: str, args: Dict, user_id: str) -> str:
        """请求人工审批"""
        import uuid
        approval_id = str(uuid.uuid4())
        self._pending_approvals[approval_id] = {
            "tool": tool_name,
            "args": args,
            "user_id": user_id,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }
        # 触发审批通知
        self._notify_approval(approval_id, tool_name, args)
        return approval_id

    def approve(self, approval_id: str) -> bool:
        """批准工具调用"""
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "approved"
            return True
        return False

    def reject(self, approval_id: str) -> bool:
        """拒绝工具调用"""
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "rejected"
            return True
        return False

    def _notify_approval(self, approval_id: str, tool_name: str, args: Dict):
        """发送审批通知（可扩展为WebSocket/邮件/Webhook）"""
        print(f"🔔 需要审批: {tool_name}")
        print(f"   参数: {json.dumps(args, ensure_ascii=False)[:200]}")
        print(f"   审批ID: {approval_id}")
        print(f"   批准: tool_guard.approve('{approval_id}')")
        print(f"   拒绝: tool_guard.reject('{approval_id}')")


# 单例
_tool_guard: Optional[ToolGuard] = None


def get_tool_guard() -> ToolGuard:
    global _tool_guard
    if _tool_guard is None:
        _tool_guard = ToolGuard()
    return _tool_guard