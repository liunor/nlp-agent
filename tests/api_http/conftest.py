"""Fixtures for the opt-in real HTTP API suite."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from .support.auth import LoginResult, login, refresh_session, same_origin_headers
from .support.coverage import build_coverage_report, format_coverage_report
from .support.database import MySqlProbe
from .support.environment import ManagedHttpEnvironment, SeededUser
from .support.redis import RedisProbe


def _record_http_request(environment: ManagedHttpEnvironment):
    def record(request: httpx.Request) -> None:
        environment.record_http_operation(
            request.method,
            request.url.path,
            port=request.url.port,
        )

    return record


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    full_selected = "api_full" in str(config.getoption("-m"))
    skip = pytest.mark.skip(reason="real HTTP suite is opt-in; run with RUN_API_HTTP=1")
    for item in items:
        if "tests/api_http" in str(item.fspath).replace("\\", "/"):
            if os.environ.get("RUN_API_HTTP") != "1":
                item.add_marker(skip)
            elif full_selected:
                # FULL is the complete suite, not a second copy of every
                # existing smoke/core test.  This marker makes the existing
                # tests part of the full selection without changing their
                # narrower marker semantics.
                item.add_marker(pytest.mark.api_full)


@pytest.fixture(scope="session")
def api_http_environment() -> ManagedHttpEnvironment:
    environment = ManagedHttpEnvironment.start()
    try:
        yield environment
    finally:
        environment.close()


@pytest.fixture(scope="session", autouse=True)
def api_full_coverage_gate(
    request: pytest.FixtureRequest,
    api_http_environment: ManagedHttpEnvironment,
) -> None:
    """Run the registry gate after every selected FULL test has run."""

    yield
    if (
        "api_full" not in str(request.config.getoption("-m"))
        or os.environ.get("API_HTTP_FULL_GATE", "1") != "1"
    ):
        return
    with httpx.Client(timeout=5) as client:
        report = build_coverage_report(
            client,
            web_base_url=api_http_environment.web_base_url,
            monitor_base_url=api_http_environment.monitor_base_url,
            observed=api_http_environment.observed_http_operations,
        )
    summary = format_coverage_report(report)
    websocket_expected = {"/ws/v1", "/ws/observability"}
    websocket_missing = websocket_expected - api_http_environment.observed_websocket_routes
    websocket_summary = (
        f"WebSocket: {len(websocket_expected - websocket_missing)} / "
        f"{len(websocket_expected)} covered"
    )
    report_text = f"{summary}\n{websocket_summary}\n"
    print(report_text, end="")
    report_path = os.environ.get("API_HTTP_COVERAGE_REPORT", "").strip()
    if report_path:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report_text, encoding="utf-8")
    assert not report.failures, "\n".join(report.failures)
    assert not websocket_missing, f"WebSocket routes without coverage: {sorted(websocket_missing)}"


@pytest.fixture(scope="session")
def web_base_url(api_http_environment: ManagedHttpEnvironment) -> str:
    return api_http_environment.web_base_url


@pytest.fixture(scope="session")
def monitor_base_url(api_http_environment: ManagedHttpEnvironment) -> str:
    return api_http_environment.monitor_base_url


@pytest.fixture(scope="session")
def database(api_http_environment: ManagedHttpEnvironment) -> ManagedHttpEnvironment:
    """The isolated migrated database; container teardown is its cleanup."""

    return api_http_environment


@pytest.fixture(scope="session")
def mysql_probe(api_http_environment: ManagedHttpEnvironment) -> MySqlProbe:
    probe = MySqlProbe(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    try:
        yield probe
    finally:
        probe.close()


@pytest.fixture(scope="session")
def redis_probe(api_http_environment: ManagedHttpEnvironment) -> RedisProbe:
    probe = RedisProbe(
        port=api_http_environment.redis_port,
        database=api_http_environment.redis_db,
        task_stream=api_http_environment.redis_task_stream,
        task_group=api_http_environment.redis_task_group,
    )
    try:
        yield probe
    finally:
        probe.close()


@pytest.fixture(scope="session")
def guest_user(api_http_environment: ManagedHttpEnvironment) -> SeededUser:
    return api_http_environment.users["guest"]


@pytest.fixture(scope="session")
def student_user(api_http_environment: ManagedHttpEnvironment) -> SeededUser:
    return api_http_environment.users["student"]


@pytest.fixture(scope="session")
def teacher_user(api_http_environment: ManagedHttpEnvironment) -> SeededUser:
    return api_http_environment.users["teacher"]


@pytest.fixture(scope="session")
def developer_user(api_http_environment: ManagedHttpEnvironment) -> SeededUser:
    return api_http_environment.users["developer"]


@pytest.fixture
def api_http_client_factory(api_http_environment: ManagedHttpEnvironment):
    clients: list[httpx.Client] = []

    def factory(
        base_url: str | httpx.URL | None = None,
        *,
        timeout: httpx.Timeout | float = httpx.Timeout(15.0, connect=5.0),
        follow_redirects: bool = True,
    ) -> httpx.Client:
        client = httpx.Client(
            base_url=api_http_environment.web_base_url if base_url is None else base_url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            event_hooks={"request": [_record_http_request(api_http_environment)]},
        )
        client._api_http_environment = api_http_environment
        clients.append(client)
        return client

    try:
        yield factory
    finally:
        for client in reversed(clients):
            client.close()


@pytest.fixture
def http_client(api_http_client_factory):
    yield api_http_client_factory()


@pytest.fixture
def authenticated_client_for(
    api_http_environment: ManagedHttpEnvironment,
    api_http_client_factory,
) -> Callable[[Any], httpx.Client]:
    def factory(user: Any) -> httpx.Client:
        client = api_http_client_factory()
        result = login(
            client,
            origin=api_http_environment.web_origin,
            username=user.username,
            password=user.password,
        )
        refreshed = refresh_session(
            client,
            origin=api_http_environment.web_origin,
            login_result=result,
        )
        client.headers.update(
            same_origin_headers(
                api_http_environment.web_origin,
                csrf_token=refreshed.csrf_token,
            )
        )
        return client

    yield factory


@pytest.fixture
def monitor_http_client(api_http_client_factory, api_http_environment: ManagedHttpEnvironment):
    yield api_http_client_factory(base_url=api_http_environment.monitor_base_url)


@pytest.fixture
def real_login(
    http_client: httpx.Client,
    api_http_environment: ManagedHttpEnvironment,
    student_user: SeededUser,
) -> LoginResult:
    return login(
        http_client,
        origin=api_http_environment.web_origin,
        username=student_user.username,
        password=student_user.password,
    )


@pytest.fixture
def authenticated_client(
    http_client: httpx.Client,
    api_http_environment: ManagedHttpEnvironment,
    real_login: LoginResult,
) -> httpx.Client:
    refreshed = refresh_session(
        http_client,
        origin=api_http_environment.web_origin,
        login_result=real_login,
    )
    http_client.headers.update(
        same_origin_headers(
            api_http_environment.web_origin,
            csrf_token=refreshed.csrf_token,
        )
    )
    return http_client
