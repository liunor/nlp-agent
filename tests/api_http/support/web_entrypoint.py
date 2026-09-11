"""Web entrypoint with deterministic Phase 4/6-only test seams."""

import os
from pathlib import Path

from core import runtime_config


# Developer write APIs persist runtime overrides and Skills.  Point those
# process-local paths at the managed HTTP-test directory before importing the
# application, so a real HTTP run cannot modify the developer's checkout.
if override_path := os.environ.get("NLP_AGENT_API_HTTP_RUNTIME_OVERRIDES"):
    runtime_config.OVERRIDE_PATH = Path(override_path)


if os.environ.get("NLP_AGENT_API_HTTP_MCP_STUB") == "1":
    import server.web.developer_runtime as _developer_runtime

    async def _stub_test_mcp_server(name: str, config: dict) -> dict[str, object]:
        from core.tool_config import MCPServerConfig

        MCPServerConfig.model_validate(config)
        return {"ok": True, "server": name, "tools": ["phase6_stub_tool"]}

    async def _stub_reload_runtime(*, reload_mcp: bool = False, reload_skills: bool = False) -> dict[str, object]:
        from configs.settings import settings
        from core.runtime_config import load_runtime_config
        from core.skill_loader import skill_loader
        from core.tool_registry import physical_tool_manager

        settings._config = load_runtime_config()
        physical_tool_manager.refresh_config()
        if reload_skills:
            skill_loader.profiles = physical_tool_manager.config.worker_profiles
            skill_loader.reload()
        return {
            "catalog_revision": physical_tool_manager.catalog_revision,
            "mcp_reloaded": reload_mcp,
            "skills_reloaded": reload_skills,
            "restart_required": False,
        }

    _developer_runtime.test_mcp_server = _stub_test_mcp_server
    _developer_runtime.reload_runtime = _stub_reload_runtime

from server.web.app import app


def _install_route_converter_metadata() -> None:
    """Expose Starlette converters that OpenAPI intentionally omits."""

    from fastapi.routing import APIRoute

    original_openapi = app.openapi

    def openapi_with_route_converters():
        document = original_openapi()
        converters: dict[str, dict[str, str]] = {}
        for route in app.routes:
            if not isinstance(route, APIRoute) or not route.param_convertors:
                continue
            route_converters = {
                name: type(convertor).__name__.removesuffix("Convertor").lower()
                for name, convertor in route.param_convertors.items()
            }
            for method in route.methods or ():
                converters[f"{method.upper()} {route.path_format}"] = route_converters
        document["x-api-http-route-converters"] = converters
        return document

    app.openapi = openapi_with_route_converters


_install_route_converter_metadata()

from .llm import DeterministicTeacherAiModel
from .sandbox import DeterministicSandboxManager


_sandbox_manager = DeterministicSandboxManager()
if os.environ.get("NLP_AGENT_SANDBOX_RUNTIME_MODE", "").strip().lower() == "docker":
    from server.sandbox import manager_rpc

    def _create_test_sandbox_manager():
        _sandbox_manager.bind(app.state.gateway.authorization_session_factory)
        return _sandbox_manager

    manager_rpc.create_sandbox_manager_rpc_client = _create_test_sandbox_manager


if skills_root := os.environ.get("NLP_AGENT_API_HTTP_SKILLS_ROOT"):
    import core.skill_loader as skill_loader_module
    import server.web.developer_runtime as developer_runtime

    skill_loader_module.skill_loader.workspace_skills_dir = Path(skills_root)
    developer_runtime.BASE_DIR = Path(skills_root).parents[1]
    skill_loader_module.skill_loader.reload()


_teacher_ai_model = DeterministicTeacherAiModel()
app.state.teacher_ai_model_factory = lambda _material: _teacher_ai_model
