from pathlib import Path

import httpx
import pytest
import yaml

from configs.settings import Settings


def test_web_network_settings_can_be_overridden_by_environment_values():
    settings = Settings(
        NLP_AGENT_WEB_HOST="0.0.0.0",
        NLP_AGENT_WEB_PORT=9876,
        NLP_AGENT_WEB_ALLOWED_HOSTS="nova.internal, 10.0.0.8",
        NLP_AGENT_WEB_ALLOWED_ORIGINS="http://nova.internal, http://10.0.0.8:9876",
    )

    runtime = settings.web_runtime

    assert runtime["host"] == "0.0.0.0"
    assert runtime["port"] == 9876
    assert runtime["allowed_hosts"] == ["nova.internal", "10.0.0.8"]
    assert runtime["allowed_origins"] == ["http://nova.internal", "http://10.0.0.8:9876"]


def test_monitor_network_settings_can_be_overridden_by_environment_values():
    settings = Settings(
        NLP_AGENT_MONITOR_HOST="0.0.0.0",
        NLP_AGENT_MONITOR_PORT=9877,
        NLP_AGENT_MONITOR_ALLOWED_HOSTS="monitor.internal",
        NLP_AGENT_MONITOR_ALLOWED_ORIGINS="http://monitor.internal",
    )

    runtime = settings.monitor_runtime

    assert runtime["host"] == "0.0.0.0"
    assert runtime["port"] == 9877
    assert runtime["allowed_hosts"] == ["monitor.internal"]
    assert runtime["allowed_origins"] == ["http://monitor.internal"]


def test_compose_runs_database_bootstrap_before_application_services_start():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )

    mysql = compose["services"]["mysql"]
    migrate = compose["services"]["nova-migrate"]

    assert mysql["image"] == "mysql:8.4"
    assert "ports" not in mysql
    assert mysql["healthcheck"]
    assert migrate["command"] == [
        ".venv/bin/python",
        "main.py",
        "bootstrap-db",
    ]
    assert migrate["depends_on"]["mysql"]["condition"] == "service_healthy"
    for service_name in ("nova-web", "nova-worker", "nova-monitor"):
        assert (
            compose["services"][service_name]["depends_on"]["nova-migrate"]["condition"]
            == "service_completed_successfully"
        )


def test_compose_passes_outbound_proxy_to_web_and_worker_containers():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )

    for service_name in ("nova-web", "nova-worker"):
        service = compose["services"][service_name]
        assert "host.docker.internal:host-gateway" in service["extra_hosts"]
        environment = service["environment"]
        assert environment["HTTP_PROXY"] == "${NOVA_OUTBOUND_PROXY_URL:-}"
        assert environment["HTTPS_PROXY"] == "${NOVA_OUTBOUND_PROXY_URL:-}"
        assert environment["NOVA_OUTBOUND_PROXY_URL"] == "${NOVA_OUTBOUND_PROXY_URL:-}"
        assert environment["NOVA_DIRECT_DOMAINS"].startswith("${NOVA_DIRECT_DOMAINS:-")
        assert "${NOVA_DIRECT_DOMAINS:-" in environment["NO_PROXY"]
        assert "redis" in environment["NO_PROXY"]
        assert "mysql" in environment["NO_PROXY"]


@pytest.mark.asyncio
async def test_compose_default_no_proxy_is_accepted_by_httpx(monkeypatch):
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )
    environment = compose["services"]["nova-web"]["environment"]
    direct_domains_expression = environment["NOVA_DIRECT_DOMAINS"]
    default_direct_domains = direct_domains_expression.removeprefix(
        "${NOVA_DIRECT_DOMAINS:-"
    ).removesuffix("}")
    rendered_no_proxy = environment["NO_PROXY"].replace(
        direct_domains_expression, default_direct_domains
    )

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", rendered_no_proxy)
    monkeypatch.setenv("no_proxy", rendered_no_proxy)

    client = httpx.AsyncClient()
    try:
        assert client._transport_for_url(
            httpx.URL("https://ses.tencentcloudapi.com")
        ) is client._transport
        assert client._transport_for_url(
            httpx.URL("https://www.example.com")
        ) is not client._transport
    finally:
        await client.aclose()


def test_model_provider_api_key_settings_bind_defaults_and_env_overrides(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    default_settings = Settings(_env_file=None)
    assert default_settings.KIMI_API_KEY == ""
    assert default_settings.GLM_API_KEY == ""

    monkeypatch.setenv("KIMI_API_KEY", "test-kimi-key")
    monkeypatch.setenv("GLM_API_KEY", "test-glm-key")
    configured = Settings(_env_file=None)
    assert configured.KIMI_API_KEY == "test-kimi-key"
    assert configured.GLM_API_KEY == "test-glm-key"
    assert configured._get_llm_config("worker-kimi")["api_key_env"] == "KIMI_API_KEY"
    assert configured._get_llm_config("worker-glm")["api_key_env"] == "GLM_API_KEY"


def test_cli_config_error_names_the_selected_provider_key(monkeypatch, capsys):
    from types import SimpleNamespace

    import configs.settings as settings_module
    from main import check_config

    monkeypatch.setattr(
        settings_module,
        "settings",
        SimpleNamespace(
            planner_llm={
                "model_id": "glm-5.3",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key_env": "GLM_API_KEY",
                "api_key_configured": False,
            },
            tool_llm={"model_id": "glm-5.3"},
        ),
    )

    assert check_config() is False
    assert "Missing GLM_API_KEY" in capsys.readouterr().out


def test_deployment_and_example_env_templates_include_kimi_and_glm_api_keys():
    root = Path(__file__).resolve().parents[1]
    templates = [
        root / ".env-example",
        root / "deploy" / "env" / "production.env.example",
        root / "deploy" / "env" / "test.env.example",
    ]
    for template_path in templates:
        assert template_path.exists(), f"Missing template file: {template_path}"
        content = template_path.read_text(encoding="utf-8")
        assert "KIMI_API_KEY" in content, f"Missing KIMI_API_KEY in {template_path}"
        assert "GLM_API_KEY" in content, f"Missing GLM_API_KEY in {template_path}"

    local_env = root / ".env"
    if local_env.exists():
        content = local_env.read_text(encoding="utf-8")
        assert "KIMI_API_KEY" in content, f"Missing KIMI_API_KEY in {local_env}"
        assert "GLM_API_KEY" in content, f"Missing GLM_API_KEY in {local_env}"


def test_compose_persists_and_exposes_structured_service_logs():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )

    for service_name in (
        "nova-migrate",
        "nova-web",
        "nova-worker",
        "nova-monitor",
        "nova-sandbox-manager",
    ):
        service = compose["services"][service_name]
        assert "nova-logs:/app/logs" in service["volumes"]
        environment = service["environment"]
        assert environment["NLP_AGENT_LOG_DIR"] == "/app/logs"
        assert environment["NLP_AGENT_LOG_SERVICE"] == service_name
        assert environment["NLP_AGENT_LOG_STDOUT"] == "${NLP_AGENT_LOG_STDOUT:-true}"

    assert "nova-logs" in compose["volumes"]


def test_compose_requires_deployment_ports_and_scopes_manager_runtime_namespace():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert "NOVA_WEB_HOST_PORT:?" in compose["services"]["nginx"]["ports"][0]
    assert "NOVA_MONITOR_HOST_PORT:?" in compose["services"]["nova-monitor"]["ports"][0]
    manager = compose["services"]["nova-sandbox-manager"]
    assert manager["environment"]["NLP_AGENT_SANDBOX_NAMESPACE"].startswith(
        "${NLP_AGENT_SANDBOX_NAMESPACE:?"
    )
    assert any("/run/nova-sandbox-lock" in volume for volume in manager["volumes"])
    monitor = compose["services"]["nova-monitor"]
    assert monitor["environment"]["NLP_AGENT_REDIS_URL"] == "redis://redis:6379/0"
    assert monitor["depends_on"]["redis"]["condition"] == "service_healthy"


def test_environment_templates_declare_distinct_sandbox_namespaces():
    root = Path(__file__).resolve().parents[1]
    test_env = (root / "deploy" / "env" / "test.env.example").read_text(encoding="utf-8")
    prod_env = (root / "deploy" / "env" / "production.env.example").read_text(encoding="utf-8")
    assert 'NLP_AGENT_SANDBOX_NAMESPACE="test"' in test_env
    assert 'NLP_AGENT_SANDBOX_NAMESPACE="prod"' in prod_env
    assert 'NOVA_SANDBOX_HOST_LOCK_DIR="/var/lock/nova-sandbox"' in test_env
    assert 'NOVA_SANDBOX_HOST_LOCK_DIR="/var/lock/nova-sandbox"' in prod_env


def test_builtin_nginx_routes_monitor_spa_api_and_websocket_to_monitor_service():
    root = Path(__file__).resolve().parents[1]
    nginx = (root / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "upstream nova_monitor" in nginx
    assert "location /monitor-api/" in nginx
    assert "proxy_pass http://nova_monitor/api/;" in nginx
    assert "location /monitor/" in nginx
    assert "proxy_pass http://nova_monitor/;" in nginx
    assert "location = /ws/observability" in nginx
    assert "proxy_pass http://nova_monitor;" in nginx
