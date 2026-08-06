"""
安全审计日志模块
"""
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, log_dir: str = "logs/audit"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def log(self, event_type: str, data: Dict[str, Any]):
        """记录审计事件"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "data": data
        }

        log_file = os.path.join(
            self.log_dir,
            f"{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        )

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_tool_call(self, tool_name: str, args: dict, verdict: str,
                      reason: str, user_id: Optional[str], session_id: Optional[str]):
        """记录工具调用审计"""
        self.log("tool_call", {
            "tool": tool_name,
            "args": self._sanitize_args(args),
            "verdict": verdict,
            "reason": reason,
            "user_id": user_id,
            "session_id": session_id
        })

    def log_security_event(self, event_type: str, details: dict):
        """记录安全事件"""
        self.log(f"security_{event_type}", details)

    def _sanitize_args(self, args: dict) -> dict:
        """脱敏敏感参数"""
        sensitive_keys = {"password", "secret", "token", "key", "credential", "api_key"}
        sanitized = {}
        for k, v in args.items():
            if k.lower() in sensitive_keys:
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = v
        return sanitized


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        log_dir = os.getenv("AUDIT_LOG_DIR", "logs/audit")
        _audit_logger = AuditLogger(log_dir)
    return _audit_logger