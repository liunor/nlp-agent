"""
身份与权限管理模块
为每个会话生成唯一的智能体身份 (AgentIdentity)，并管理其权限。
"""
import secrets
import hashlib
from typing import Optional, Set
from dataclasses import dataclass, field


@dataclass
class AgentIdentity:
    """智能体身份对象"""
    agent_id: str          # 唯一身份标识（哈希生成）
    user_id: str           # 关联的用户ID
    session_id: str        # 会话ID
    permissions: Set[str] = field(default_factory=set)  # 权限集合

    def has_permission(self, permission: str) -> bool:
        """检查是否拥有某个权限"""
        return permission in self.permissions

    def grant(self, permission: str):
        """授予新权限"""
        self.permissions.add(permission)

    def revoke(self, permission: str):
        """撤销权限"""
        self.permissions.discard(permission)


class IdentityManager:
    """身份管理器（内存存储，生产环境可改用 Redis）"""

    def __init__(self):
        self._sessions: dict[str, AgentIdentity] = {}

    def create_identity(self, user_id: str, session_id: str) -> AgentIdentity:
        """
        创建并存储新的智能体身份
        :param user_id: 用户标识（例如从登录获取）
        :param session_id: 会话ID
        :return: AgentIdentity 对象
        """
        agent_id = self._generate_agent_id(user_id, session_id)
        identity = AgentIdentity(agent_id, user_id, session_id)
        self._sessions[session_id] = identity
        return identity

    def get_identity(self, session_id: str) -> Optional[AgentIdentity]:
        """根据会话ID获取身份对象"""
        return self._sessions.get(session_id)

    def revoke_identity(self, session_id: str):
        """撤销身份（登出时调用）"""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def _generate_agent_id(self, user_id: str, session_id: str) -> str:
        """生成唯一 Agent ID（基于用户ID+会话ID+随机盐的SHA256哈希）"""
        raw = f"{user_id}:{session_id}:{secrets.token_hex(8)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # 可选：权限预设（示例）
    def grant_default_permissions(self, identity: AgentIdentity):
        """为新身份授予默认权限（如基础工具调用）"""
        default_perm = {"search_docs", "get_learning_path", "evaluate_answer"}
        for p in default_perm:
            identity.grant(p)


# 单例实例（全局唯一）
_identity_manager: Optional[IdentityManager] = None

def get_identity_manager() -> IdentityManager:
    global _identity_manager
    if _identity_manager is None:
        _identity_manager = IdentityManager()
    return _identity_manager