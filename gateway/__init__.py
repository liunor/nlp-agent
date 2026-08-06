"""Backend Gateway Core public surface."""
from .engine import GatewayEngine
from .repository import SessionRepository
from gateway.contracts import (
    GatewayEvent,
    GatewayEventType,
    GatewayHealth,
    InjectMessageRequest,
    SubmitTurnRequest,
    TurnAccepted,
    TurnRecord,
    TurnStatus,
)

__all__ = [
    "GatewayEvent",
    "GatewayEventType",
    "GatewayHealth",
    "InjectMessageRequest",
    "SubmitTurnRequest",
    "TurnAccepted",
    "TurnRecord",
    "TurnStatus",
]
def init_gateway():
    """初始化网关安全依赖"""
    from security import init_security
    config = init_security()
    print(f"✅ Gateway 安全模块加载完成: {config}")
    return GatewayEngine()