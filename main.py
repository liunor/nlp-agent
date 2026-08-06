"""CLI adapter for the lifecycle-owning Backend Gateway Core."""

from __future__ import annotations

import asyncio
import sys

from core.identity import AuthenticatedPrincipal
from gateway.contracts import GatewayEventType, SubmitTurnRequest, GatewayNotStartedError
from security import init_security
from gateway.engine import LangGraphAgentEngine, GatewayEngine
from gateway.core import BackendGateway
from contextlib import asynccontextmanager
from gateway.engine import GatewayEngine
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from gateway.core import BackendGateway
from gateway.contracts import SubmitTurnRequest
backend_gateway = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global backend_gateway
    # 启动时初始化
    backend_gateway = BackendGateway()
    await backend_gateway.start()
    print("✅ Backend Gateway 已启动，安全护栏已激活")
    yield
    # 关闭时清理
    await backend_gateway.close()

app = FastAPI(lifespan=lifespan)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def check_config() -> bool:
    from configs.settings import settings

    config = settings.planner_llm
    print(f"Coordinator: {config['model_id']} ({config['base_url']})")
    print(f"Worker:      {settings.tool_llm['model_id']}")
    if not config.get("api_key_configured"):
        print("Missing DEEPSEEK_API_KEY; create .env in the project root.")
        return False
    return True


async def _input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)

async def startup():
    agent_engine = LangGraphAgentEngine()
    gateway_engine = GatewayEngine(agent_engine)
    await agent_engine.start(gateway_engine._event_sink)

async def main() -> None:

    security_config = init_security()
    print(f"安全配置: {security_config}")

    if not check_config():
        raise SystemExit(1)

    principal = AuthenticatedPrincipal(
        user_id="local", workspace_ids=frozenset({"default"}), roles=frozenset({"admin"})
    )
    gateway = BackendGateway()
    await gateway.start()
    from server.agent.session_storage import get_active_session_id

    active_session_id = get_active_session_id()
    if active_session_id:
        try:
            await gateway.sessions.resolve(principal, active_session_id)
        except (FileNotFoundError, PermissionError):
            active_session_id = None
    if not active_session_id:
        active_session_id = (
            await gateway.create_session(principal, workspace_id="default", channel="cli")
        ).session_id

    print("Enter a question. Commands: /new, /sessions, /load <id>, /exit")
    try:
        while True:
            query = (await _input("\nYou: ")).strip()
            if not query:
                continue
            if query == "/exit":
                break
            if query == "/new":
                active_session_id = (
                    await gateway.create_session(
                        principal, workspace_id="default", channel="cli"
                    )
                ).session_id
                print(f"[system] New session: {active_session_id}")
                continue
            if query == "/sessions":
                for session in await gateway.sessions.list(principal):
                    print(f"- {session['session_id']} | {session.get('last_active', 0)}")
                continue
            if query.startswith("/load "):
                target = query.split(maxsplit=1)[1]
                try:
                    await gateway.sessions.resolve(principal, target)
                except (FileNotFoundError, PermissionError):
                    print("[system] Session not found")
                else:
                    active_session_id = target
                    print(f"[system] Loaded: {target}")
                continue

            accepted = await gateway.submit_turn(
                principal,
                SubmitTurnRequest(session_id=active_session_id, content=query),
            )
            printed = False
            async for event in gateway.stream_events(principal, accepted.turn_id):
                if event.type != GatewayEventType.MESSAGE_DELTA:
                    continue
                if event.payload.get("channel") == "reasoning":
                    continue
                delta = event.payload.get("delta", "")
                if not delta:
                    continue
                if not printed:
                    print("\nAgent: ", end="", flush=True)
                    printed = True
                print(delta, end="", flush=True)
            if printed:
                print()
            else:
                turn = await gateway.get_turn(principal, accepted.turn_id)
                print(f"\nAgent: {turn.final_text or turn.error_message or ''}")
    finally:
        await gateway.close()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if command in {"serve", "web"}:
        from server.web.__main__ import run

        run()
    elif command in {"monitor", "observe"}:
        from server.monitor.__main__ import run

        run()
    elif command in {"chat", "--chat", "-c"}:
        asyncio.run(main())
    else:
        print("Usage: python main.py [chat|serve|monitor]")
        raise SystemExit(2)

@app.post("/api/v1/turns")
async def submit_turn_endpoint(request: SubmitTurnRequest, principal: AuthenticatedPrincipal = Depends(get_principal)):
    try:
        result = await backend_gateway.submit_turn(principal, request)
        return {"success": True, "turn_id": result.turn_id, "session_id": result.session_id}
    except ValueError as e:
        return {"success": False, "error": str(e), "code": "SECURITY_VIOLATION"}, 400


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    pass
# 临时模拟用户身份
class FakePrincipal:
    def __init__(self, user_id="test_user"):
        self.user_id = user_id
        self.is_admin = False
        self.workspace_ids = ["default", "*"]  # 允许所有workspace

async def get_principal():
    # 在生产环境中，这里会从请求头/Token解析用户
    # 现在返回一个固定用户
    return FakePrincipal()