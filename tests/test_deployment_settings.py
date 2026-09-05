from pathlib import Path

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


def test_compose_runs_mysql_migrations_before_application_services_start():
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
    assert migrate["command"] == [".venv/bin/python", "-m", "alembic", "upgrade", "head"]
    assert migrate["depends_on"]["mysql"]["condition"] == "service_healthy"
    for service_name in ("nova-web", "nova-worker", "nova-monitor"):
        assert (
            compose["services"][service_name]["depends_on"]["nova-migrate"]["condition"]
            == "service_completed_successfully"
        )
