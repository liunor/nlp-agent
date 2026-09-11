"""Runtime OpenAPI inventory for the Web and Monitor HTTP services."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import httpx


HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)


@dataclass(frozen=True, slots=True)
class Operation:
    service: str
    method: str
    path: str
    operation_id: str
    path_converters: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return self.service, self.method, self.path


def fetch_openapi(
    client: httpx.Client,
    *,
    service: str,
    base_url: str,
) -> tuple[dict[str, Any], list[Operation]]:
    response = client.get(f"{base_url.rstrip('/')}/api/openapi.json")
    response.raise_for_status()
    document = response.json()
    route_converters = document.get("x-api-http-route-converters", {})
    if not isinstance(route_converters, dict):
        raise ValueError("OpenAPI route converter metadata must be an object")
    operations: list[Operation] = []
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            operations.append(
                Operation(
                    service=service,
                    method=method.upper(),
                    path=path,
                    operation_id=str(operation.get("operationId", "")),
                    path_converters=_path_converters(
                        route_converters.get(f"{method.upper()} {path}")
                    ),
                )
            )
    operations.sort(key=lambda item: (item.path, item.method))
    return document, operations


def inventory_report(web: list[Operation], monitor: list[Operation]) -> str:
    all_operations = [*web, *monitor]
    return (
        f"Web operations: {len(web)}\n"
        f"Monitor operations: {len(monitor)}\n"
        f"Total: {len(all_operations)}"
    )


def operation_matches(operation: Operation, *, method: str, path: str) -> bool:
    """Match an observed concrete request against an OpenAPI path template."""
    if operation.method != method.upper():
        return False
    placeholders = list(re.finditer(r"\{[^}:]+(?::path)?\}", operation.path))
    converters = dict(operation.path_converters)
    pattern_parts: list[str] = []
    cursor = 0
    for match in placeholders:
        pattern_parts.append(re.escape(operation.path[cursor : match.start()]))
        parameter = match.group(0)[1:-1].split(":", 1)[0]
        is_path_parameter = (
            converters.get(parameter) == "path"
            or match.group(0).endswith(":path}")
        )
        pattern_parts.append(".*" if is_path_parameter else "[^/]+")
        cursor = match.end()
    pattern_parts.append(re.escape(operation.path[cursor:]))
    return re.fullmatch("".join(pattern_parts), path) is not None


def _path_converters(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(
        sorted(
            (str(name), str(converter).strip().lower())
            for name, converter in value.items()
            if str(name).strip() and str(converter).strip()
        )
    )
