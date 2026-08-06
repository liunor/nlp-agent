"""
技能执行器 - 带安全护栏
"""
import logging
from typing import Any, Dict
from security.tool_guard import get_tool_guard, Verdict
from security.audit import get_audit_logger

logger = logging.getLogger(__name__)


class SkillExecutor:
    """技能执行器 - 带安全护栏"""

    def __init__(self):
        self.tool_guard = get_tool_guard()
        self.audit_logger = get_audit_logger()

    def execute(self, tool_name: str, tool_args: Dict[str, Any],
                user_id: str = None, session_id: str = None) -> Any:
        """
        执行工具调用，带安全护栏
        """
        # 1. 安全评估
        verdict, reason = self.tool_guard.evaluate(tool_name, tool_args)

        # 2. 审计日志
        self.audit_logger.log_tool_call(
            tool_name=tool_name,
            args=tool_args,
            verdict=verdict.value,
            reason=reason,
            user_id=user_id,
            session_id=session_id
        )

        # 3. 根据决策执行
        if verdict == Verdict.BLOCK:
            logger.warning(f"工具调用被阻止: {tool_name} - {reason}")
            raise SecurityError(f"工具调用被安全策略阻止: {reason}")

        if verdict == Verdict.AUTH:
            # 需要人工审批
            approval_id = self.tool_guard.request_approval(tool_name, tool_args, user_id)
            if not self._wait_for_approval(approval_id):
                raise SecurityError(f"工具调用未获得审批: {tool_name}")
            logger.info(f"工具调用已获审批: {tool_name}")

        # 4. PASS - 执行
        logger.info(f"工具调用已授权: {tool_name}")
        return self._do_execute(tool_name, tool_args)

    def _wait_for_approval(self, approval_id: str, timeout: int = 60) -> bool:
        """等待审批结果"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            if approval_id in self.tool_guard._pending_approvals:
                status = self.tool_guard._pending_approvals[approval_id]["status"]
                if status == "approved":
                    return True
                if status == "rejected":
                    return False
            time.sleep(0.5)
        return False

    def _do_execute(self, tool_name: str, args: Dict) -> Any:
        """实际执行工具"""
        # 根据工具名路由到具体实现
        # 例如: getattr(self, f"_exec_{tool_name}")(args)
        pass


class SecurityError(Exception):
    """安全相关异常"""
    pass