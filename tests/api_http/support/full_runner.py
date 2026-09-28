"""OpenAPI-driven request generation for the real HTTP FULL tier."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
import json
import re
from typing import Any

import httpx

from .inventory import Operation


_PLACEHOLDER = re.compile(r"\{([^}:]+)(?::path)?\}")


@dataclass(frozen=True, slots=True)
class FullProbeContext:
    """Stable values used to make an isolated, deterministic probe request."""

    workspace_id: str
    user_id: str
    session_id: str
    nonce: str
    username: str = "api-full-probe"
    password: str = "ApiHttp-password-123!"
    csrf_token: str = "api-full-csrf-token"
    knowledge_point_id: str = "knowledge-point-a"
    monitor_csrf_token: str | None = None

    def csrf_token_for(self, service: str) -> str:
        if service == "monitor" and self.monitor_csrf_token:
            return self.monitor_csrf_token
        return self.csrf_token


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    method: str
    path: str
    params: dict[str, str]
    headers: dict[str, str]
    json_body: Any | None = None
    data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    operation: Operation
    status_code: int | None
    response_text: str
    failure: str | None

    def describe(self) -> str:
        status = "transport-error" if self.status_code is None else str(self.status_code)
        detail = self.response_text.strip().replace("\r", " ").replace("\n", " ")
        if len(detail) > 240:
            detail = detail[:240] + "..."
        suffix = f" body={detail!r}" if detail else ""
        return f"{self.operation.service} {self.operation.method} {self.operation.path}: {self.failure} ({status}){suffix}"


def build_probe_request(
    operation: Operation,
    document: dict[str, Any],
    *,
    context: FullProbeContext,
) -> ProbeRequest:
    """Build one validly-shaped request from a live OpenAPI operation."""

    operation_document = _operation_document(operation, document)
    parameters = _parameters(document, operation, operation_document)
    path = operation.path
    for match in _PLACEHOLDER.finditer(operation.path):
        name = match.group(1)
        path = path.replace(
            match.group(0),
            _parameter_value(name, context, path_parameter=True),
            1,
        )

    query: dict[str, str] = {}
    headers: dict[str, str] = {}
    for parameter in parameters:
        location = str(parameter.get("in", ""))
        name = str(parameter.get("name", ""))
        if not name or location == "path":
            continue
        value = (
            context.csrf_token_for(operation.service)
            if name.lower().replace("-", "_") in {"csrf_token", "x_csrf_token"}
            else _parameter_value(name, context)
        )
        if location == "query" and (parameter.get("required") or _include_optional_query(name)):
            query[name] = value
        elif location == "header":
            headers[name] = value

    json_body: Any | None = None
    data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None
    request_body = operation_document.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content", {})
        if isinstance(content, dict):
            media_type, media = _preferred_media_type(content)
            if media_type is not None and isinstance(media, dict):
                schema = media.get("schema", {})
                if media_type == "multipart/form-data":
                    data, files = _multipart_body(schema, document, context)
                elif media_type == "application/x-www-form-urlencoded":
                    value = _schema_value(schema, document, context)
                    data = _form_values(value)
                else:
                    json_body = _schema_value(schema, document, context)

    return ProbeRequest(
        method=operation.method,
        path=path,
        params=query,
        headers=headers,
        json_body=json_body,
        data=data,
        files=files,
    )


def classify_probe_response(operation: Operation, response: Any) -> str | None:
    """Return a failure reason for transport/mount failures, if any.

    Authentication, authorization, validation, and resource-not-found responses
    are useful reachability outcomes for a generic probe. A 404 on a static
    operation or any 5xx/405 response indicates a broken mount or server.
    """

    status_code = int(response.status_code)
    if status_code == 405:
        return "method not mounted"
    if 500 <= status_code <= 599:
        return f"server returned {status_code}"
    if status_code == 404 and not _PLACEHOLDER.search(operation.path):
        return "static operation returned 404"
    return None


def run_full_probes(
    services: Mapping[str, tuple[httpx.Client, dict[str, Any], list[Operation]]],
    *,
    context: FullProbeContext,
    skip_keys: Collection[tuple[str, str, str]] = (),
    record_probe: Callable[[tuple[str, str, str]], None] | None = None,
) -> tuple[ProbeResult, ...]:
    """Dispatch one real network request for every supplied live operation."""

    results: list[ProbeResult] = []
    skipped = set(skip_keys)
    for service, (client, document, operations) in services.items():
        for operation in operations:
            if operation.key in skipped:
                continue
            request = build_probe_request(operation, document, context=context)
            if record_probe is not None:
                record_probe(operation.key)
            kwargs: dict[str, Any] = {
                "headers": request.headers,
                "params": request.params,
            }
            if request.files is not None:
                kwargs["files"] = request.files
                kwargs["data"] = request.data or {}
            elif request.data is not None:
                kwargs["data"] = request.data
            elif request.json_body is not None:
                kwargs["json"] = request.json_body
            try:
                response = client.request(request.method, request.path, **kwargs)
            except httpx.HTTPError as error:
                results.append(
                    ProbeResult(
                        operation=operation,
                        status_code=None,
                        response_text=str(error),
                        failure=f"transport error: {type(error).__name__}",
                    )
                )
                continue
            results.append(
                ProbeResult(
                    operation=operation,
                    status_code=response.status_code,
                    response_text=response.text,
                    failure=classify_probe_response(operation, response),
                )
            )
    return tuple(results)


def _operation_document(
    operation: Operation,
    document: dict[str, Any],
) -> dict[str, Any]:
    paths = document.get("paths", {})
    path_item = paths.get(operation.path, {}) if isinstance(paths, dict) else {}
    operation_document = (
        path_item.get(operation.method.lower(), {})
        if isinstance(path_item, dict)
        else {}
    )
    return operation_document if isinstance(operation_document, dict) else {}


def _parameters(
    document: dict[str, Any],
    operation: Operation,
    operation_document: dict[str, Any],
) -> list[dict[str, Any]]:
    paths = document.get("paths", {})
    path_item = paths.get(operation.path, {}) if isinstance(paths, dict) else {}
    values: list[dict[str, Any]] = []
    for source in (
        path_item.get("parameters", []) if isinstance(path_item, dict) else [],
        operation_document.get("parameters", []),
    ):
        if isinstance(source, list):
            values.extend(item for item in source if isinstance(item, dict))
    return values


def _preferred_media_type(content: dict[str, Any]) -> tuple[str | None, Any]:
    for media_type in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        if media_type in content:
            return media_type, content[media_type]
    return next(iter(content.items()), (None, None))


def _multipart_body(
    schema: Any,
    document: dict[str, Any],
    context: FullProbeContext,
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    resolved = _resolve_schema(schema, document)
    properties = resolved.get("properties", {}) if isinstance(resolved, dict) else {}
    data: dict[str, str] = {}
    files: dict[str, tuple[str, bytes, str]] = {}
    if not isinstance(properties, dict):
        return data, files
    for name, property_schema in properties.items():
        if not isinstance(property_schema, dict):
            continue
        resolved_property = _resolve_schema(property_schema, document)
        if resolved_property.get("format") == "binary":
            files[str(name)] = (
                "api-full-probe.png",
                b"\x89PNG\r\n\x1a\napi-full-probe",
                "image/png",
            )
        elif str(name) in resolved.get("required", []):
            data[str(name)] = _stringify(
                _schema_value(resolved_property, document, context, name=str(name))
            )
    return data, files


def _schema_value(
    schema: Any,
    document: dict[str, Any],
    context: FullProbeContext,
    *,
    name: str | None = None,
) -> Any:
    resolved = _resolve_schema(schema, document)
    if not resolved:
        return {}
    for key in ("example", "default", "const"):
        if key in resolved:
            return resolved[key]
    for branch_key in ("oneOf", "anyOf", "allOf"):
        branches = resolved.get(branch_key)
        if isinstance(branches, list) and branches:
            if branch_key == "allOf":
                result: dict[str, Any] = {}
                for branch in branches:
                    value = _schema_value(branch, document, context, name=name)
                    if isinstance(value, dict):
                        result.update(value)
                return result
            return _schema_value(branches[0], document, context, name=name)
    enum = resolved.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    schema_type = resolved.get("type")
    if schema_type == "object" or "properties" in resolved:
        properties = resolved.get("properties", {})
        required = set(resolved.get("required", []))
        if not isinstance(properties, dict):
            return {}
        return {
            str(property_name): _schema_value(
                property_schema,
                document,
                context,
                name=str(property_name),
            )
            for property_name, property_schema in properties.items()
            if str(property_name) in required
            and isinstance(property_schema, (dict, str))
        }
    if schema_type == "array":
        item_schema = resolved.get("items", {})
        return [_schema_value(item_schema, document, context, name=name)]
    if schema_type == "boolean":
        return False
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "string":
        return _parameter_value(name or "value", context)
    return {}


def _resolve_schema(schema: Any, document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        schemas = document.get("components", {}).get("schemas", {})
        target = schemas.get(name) if isinstance(schemas, dict) else None
        return _resolve_schema(target, document)
    return schema


def _parameter_value(
    name: str,
    context: FullProbeContext,
    *,
    path_parameter: bool = False,
) -> str:
    normalized = name.lower().replace("-", "_")
    if normalized in {"workspace_id", "workspace"}:
        return context.workspace_id
    if normalized in {"user_id", "actor_user_id"}:
        return context.user_id
    if normalized in {"session_id", "conversation_id"}:
        return context.session_id
    if normalized in {"knowledge_point_id", "point_id"}:
        return context.knowledge_point_id
    if normalized in {"csrf_token", "x_csrf_token"}:
        return context.csrf_token
    if normalized in {"username", "login", "account"}:
        return context.username
    if normalized in {"password", "current_password", "new_password"}:
        return context.password
    if normalized in {"email"}:
        return "probe-user@example.com"
    if normalized in {"email_code"}:
        return "123456"
    if normalized in {"limit"}:
        return "1"
    if normalized in {"offset"}:
        return "0"
    if normalized in {"days", "window_minutes", "bucket_minutes", "since_seconds"}:
        return "1"
    if normalized in {"period_start", "period_end", "effective_from", "effective_until", "expires_at", "released_at", "billed_at", "occurred_at", "before"}:
        return "2026-01-01T00:00:00Z"
    if normalized in {"start_date", "end_date"}:
        return "2026-01-01" if normalized == "start_date" else "2026-01-02"
    if normalized in {"start", "end"}:
        return "2026-01-01" if normalized == "start" else "2026-01-02"
    if normalized in {"include_deleted", "include_users"}:
        return "false"
    if normalized == "asset_path":
        return "assets/api-full-probe.png"
    if normalized == "file_name":
        return "api-full-probe.png"
    if normalized in {"kind"}:
        return "exercise"
    if normalized in {"role_code"}:
        return "student"
    if normalized in {"name", "code", "pricing_key"}:
        return f"api-full-{context.nonce[:12]}"
    if normalized == "version":
        return "1.0.0"
    if normalized in {"ticket", "after_event_id", "trace_id", "chain_id", "checkpoint_id"}:
        return context.nonce
    if normalized in {"resource"}:
        return "probe"
    if normalized in {"status", "status_filter"}:
        return "draft"
    if normalized in {"granularity"}:
        return "day"
    if normalized in {"level", "decision", "reason_code", "focus", "query"}:
        return "all"
    if path_parameter and normalized.endswith("_id"):
        return context.nonce
    return f"api-full-{context.nonce[:12]}"


def _include_optional_query(name: str) -> bool:
    return name.lower() in {"limit", "offset", "days", "include_deleted", "include_users"}


def _form_values(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"value": _stringify(value)}
    return {str(name): _stringify(item) for name, item in value.items()}


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
