"""Request every live OpenAPI operation once in the real HTTP FULL tier."""

from __future__ import annotations

import uuid

import pytest

from ..support.auth import login, refresh_session, same_origin_headers, session_id_for_client
from ..support.coverage import load_registry
from ..support.full_runner import FullProbeContext, run_full_probes
from ..support.inventory import fetch_openapi
from ..support.resources import create_user


pytestmark = pytest.mark.api_full


def test_every_live_openapi_operation_receives_a_real_http_probe(
    api_http_client_factory,
    api_http_environment,
    developer_user,
    mysql_probe,
) -> None:
    web_client = api_http_client_factory(base_url=api_http_environment.web_base_url)
    web_login = login(
        web_client,
        origin=api_http_environment.web_origin,
        username=developer_user.username,
        password=developer_user.password,
    )
    web_session = refresh_session(
        web_client,
        origin=api_http_environment.web_origin,
        login_result=web_login,
    )
    web_client.headers.update(
        same_origin_headers(
            api_http_environment.web_origin,
            csrf_token=web_session.csrf_token,
        )
    )

    monitor_client = api_http_client_factory(
        base_url=api_http_environment.monitor_base_url
    )
    monitor_login = login(
        monitor_client,
        origin=api_http_environment.monitor_base_url,
        username=developer_user.username,
        password=developer_user.password,
        cookie_name="nlp_monitor_session",
    )
    monitor_session = refresh_session(
        monitor_client,
        origin=api_http_environment.monitor_base_url,
        login_result=monitor_login,
    )
    monitor_client.headers.update(
        same_origin_headers(
            api_http_environment.monitor_base_url,
            csrf_token=monitor_session.csrf_token,
        )
    )

    web_document, web_operations = fetch_openapi(
        web_client,
        service="web",
        base_url=api_http_environment.web_base_url,
    )
    monitor_document, monitor_operations = fetch_openapi(
        monitor_client,
        service="monitor",
        base_url=api_http_environment.monitor_base_url,
    )
    probe_user = create_user(web_client, prefix="phase7full")
    context = FullProbeContext(
        workspace_id=developer_user.workspace_id,
        user_id=probe_user.user_id,
        session_id=session_id_for_client(web_client, mysql_probe),
        nonce=str(uuid.uuid4()),
        username=developer_user.username,
        password=developer_user.password,
        csrf_token=web_session.csrf_token,
        monitor_csrf_token=monitor_session.csrf_token,
    )
    exempt = {entry.key for entry in load_registry() if entry.exempt}
    results = run_full_probes(
        {
            "web": (web_client, web_document, web_operations),
            "monitor": (monitor_client, monitor_document, monitor_operations),
        },
        context=context,
        skip_keys=exempt,
        record_probe=api_http_environment.record_full_probe,
    )

    failures = [result for result in results if result.failure is not None]
    assert len(results) == len(web_operations) + len(monitor_operations) - len(exempt)
    assert not failures, "\n".join(result.describe() for result in failures)
