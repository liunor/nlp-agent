from __future__ import annotations

import httpx
import pytest

from tests.api_http.support.inventory import Operation
from tests.api_http.support.inventory import observed_operation_keys
from tests.api_http.support.full_runner import (
    FullProbeContext,
    build_probe_request,
    classify_probe_response,
)


def _operation(
    method: str,
    path: str,
    *,
    converters: tuple[tuple[str, str], ...] = (),
) -> Operation:
    return Operation(
        service="web",
        method=method,
        path=path,
        operation_id="test_operation",
        path_converters=converters,
    )


def test_probe_request_uses_valid_path_and_query_examples() -> None:
    document = {
        "paths": {
            "/api/v1/teacher/{workspace_id}/pages/{knowledge_point_id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "workspace_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "knowledge_point_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer"},
                        },
                    ]
                }
            }
        }
    }
    operation = _operation(
        "GET",
        "/api/v1/teacher/{workspace_id}/pages/{knowledge_point_id}",
    )

    request = build_probe_request(
        operation,
        document,
        context=FullProbeContext(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            nonce="nonce-a",
        ),
    )

    assert request.method == "GET"
    assert request.path == "/api/v1/teacher/workspace-a/pages/knowledge-point-a"
    assert request.params == {"limit": "1"}
    assert request.json_body is None


def test_probe_request_resolves_required_json_schema_fields() -> None:
    document = {
        "components": {
            "schemas": {
                "CreateSessionBody": {
                    "type": "object",
                    "required": ["workspace_id"],
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "title": {"type": "string"},
                    },
                }
            }
        },
        "paths": {
            "/api/v1/sessions": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CreateSessionBody"
                                }
                            }
                        },
                    }
                }
            }
        },
    }

    request = build_probe_request(
        _operation("POST", "/api/v1/sessions"),
        document,
        context=FullProbeContext(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            nonce="nonce-a",
        ),
    )

    assert request.json_body == {"workspace_id": "workspace-a"}


def test_probe_request_keeps_default_file_path_parameters_single_segment() -> None:
    document = {
        "paths": {
            "/api/v1/uploads/{session_id}/{file_name}": {
                "get": {
                    "parameters": [
                        {
                            "name": "session_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "file_name",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ]
                }
            }
        }
    }

    request = build_probe_request(
        _operation("GET", "/api/v1/uploads/{session_id}/{file_name}"),
        document,
        context=FullProbeContext("workspace-a", "user-a", "session-a", "nonce-a"),
    )

    assert request.path == "/api/v1/uploads/session-a/api-full-probe.png"


def test_probe_request_builds_multipart_file_payload() -> None:
    document = {
        "components": {
            "schemas": {
                "UploadBody": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"}
                    },
                }
            }
        },
        "paths": {
            "/api/v1/uploads": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {"$ref": "#/components/schemas/UploadBody"}
                            }
                        },
                    }
                }
            }
        },
    }

    request = build_probe_request(
        _operation("POST", "/api/v1/uploads"),
        document,
        context=FullProbeContext(
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            nonce="nonce-a",
        ),
    )

    assert request.files is not None
    assert request.files["file"][0] == "api-full-probe.png"
    assert request.files["file"][2] == "image/png"
    assert request.json_body is None


@pytest.mark.parametrize(
    ("status_code", "path_has_parameter", "expected"),
    [
        (200, False, None),
        (422, False, None),
        (403, True, None),
        (404, True, None),
        (404, False, "static operation returned 404"),
        (405, True, "method not mounted"),
        (500, True, "server returned 500"),
    ],
)
def test_probe_response_classification(
    status_code: int,
    path_has_parameter: bool,
    expected: str | None,
) -> None:
    response = httpx.Response(status_code, request=httpx.Request("GET", "http://test"))
    operation = _operation("GET", "/api/v1/resource/{resource_id}" if path_has_parameter else "/health/ready")

    assert classify_probe_response(operation, response) == expected


def test_run_full_probes_dispatches_each_non_exempt_operation() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            del kwargs
            self.calls.append((method, path))
            return httpx.Response(
                200,
                request=httpx.Request(method, f"http://test{path}"),
            )

    client = RecordingClient()
    operations = [
        _operation("GET", "/health/ready"),
        _operation("GET", "/api/v1/items/{item_id}"),
    ]

    from tests.api_http.support.full_runner import run_full_probes

    results = run_full_probes(
        {"web": (client, {"paths": {}}, operations)},
        context=FullProbeContext("workspace-a", "user-a", "session-a", "nonce-a"),
        skip_keys={operations[1].key},
    )

    assert len(results) == 1
    assert client.calls == [("GET", "/health/ready")]


def test_observed_path_is_assigned_to_the_most_specific_operation() -> None:
    literal = _operation("GET", "/api/v1/users/me")
    parameterized = _operation("GET", "/api/v1/users/{user_id}")

    covered = observed_operation_keys(
        [literal, parameterized],
        [("web", "GET", "/api/v1/users/me")],
    )

    assert covered == {literal.key}
