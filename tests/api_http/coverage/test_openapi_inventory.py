"""Live OpenAPI inventory smoke check.

The service/method/path tuple is the stable key. Operation IDs are retained
for reporting but are allowed to collide between the two separate apps.
"""

from __future__ import annotations

import pytest

from ..support.inventory import fetch_openapi, inventory_report, operation_matches


pytestmark = pytest.mark.api_smoke


def test_live_openapi_inventory_matches_current_baseline(
    http_client,
    web_base_url: str,
    monitor_base_url: str,
    capsys,
) -> None:
    _web_document, web_operations = fetch_openapi(
        http_client,
        service="web",
        base_url=web_base_url,
    )
    _monitor_document, monitor_operations = fetch_openapi(
        http_client,
        service="monitor",
        base_url=monitor_base_url,
    )
    operations = [*web_operations, *monitor_operations]
    assert web_operations
    assert monitor_operations
    assert len({operation.key for operation in operations}) == len(operations)
    assert all(operation.operation_id for operation in operations)
    asset_operation = next(
        operation
        for operation in web_operations
        if operation.path == "/api/v1/learning/book/{workspace_id}/assets/{asset_path}"
    )
    assert dict(asset_operation.path_converters)["asset_path"] == "path"
    assert operation_matches(
        asset_operation,
        method="GET",
        path="/api/v1/learning/book/workspace/assets/nested/asset.png",
    )
    user_operation = next(
        operation for operation in web_operations if operation.path == "/api/v1/users/{user_id}"
    )
    assert not operation_matches(
        user_operation,
        method="GET",
        path="/api/v1/users/a/b",
    )
    print(inventory_report(web_operations, monitor_operations))
    assert f"Total: {len(operations)}" in capsys.readouterr().out
