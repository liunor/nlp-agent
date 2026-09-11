"""Inventory-driven API coverage checks for the real HTTP suite."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from .inventory import Operation, fetch_openapi, operation_matches


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "coverage" / "api_coverage.json"


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    service: str
    method: str
    path: str
    tiers: tuple[str, ...]
    exempt: bool = False
    reason: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.service, self.method, self.path


@dataclass(frozen=True, slots=True)
class CoverageReport:
    operations: tuple[Operation, ...]
    registry: tuple[CoverageEntry, ...]
    duplicate_registry: tuple[tuple[str, str, str], ...]
    unclassified: tuple[tuple[str, str, str], ...]
    stale: tuple[tuple[str, str, str], ...]
    duplicate_openapi: tuple[tuple[str, str, str], ...]
    covered: tuple[tuple[str, str, str], ...]
    exempt: tuple[tuple[str, str, str], ...]
    missing_full: tuple[tuple[str, str, str], ...]
    uncovered: tuple[tuple[str, str, str], ...]

    @property
    def non_exempt_operations(self) -> tuple[tuple[str, str, str], ...]:
        exempt = set(self.exempt)
        return tuple(operation.key for operation in self.operations if operation.key not in exempt)

    @property
    def failures(self) -> tuple[str, ...]:
        failures: list[str] = []
        if self.duplicate_openapi:
            failures.append("duplicate OpenAPI operations: " + _format_keys(self.duplicate_openapi))
        if self.duplicate_registry:
            failures.append("duplicate registry entries: " + _format_keys(self.duplicate_registry))
        if self.unclassified:
            failures.append("unclassified API operations: " + _format_keys(self.unclassified))
        if self.stale:
            failures.append("stale registry entries: " + _format_keys(self.stale))
        if self.missing_full:
            failures.append("operations missing full tier: " + _format_keys(self.missing_full))
        if self.uncovered:
            failures.append("non-exempt operations without HTTP coverage: " + _format_keys(self.uncovered))
        return tuple(failures)


def load_registry(path: Path = REGISTRY_PATH) -> tuple[CoverageEntry, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        raise ValueError("API coverage registry must contain an operations list")

    entries: list[CoverageEntry] = []
    for index, item in enumerate(raw["operations"]):
        if not isinstance(item, dict):
            raise ValueError(f"registry entry {index} is not an object")
        service = str(item.get("service", "")).strip().lower()
        method = str(item.get("method", "")).strip().upper()
        path_value = str(item.get("path", "")).strip()
        tiers_value = item.get("tiers")
        if not service or not method or not path_value or not isinstance(tiers_value, list):
            raise ValueError(f"registry entry {index} is missing service/method/path/tiers")
        tiers = tuple(str(tier).strip().lower() for tier in tiers_value if str(tier).strip())
        exempt = bool(item.get("exempt", False))
        reason = item.get("reason")
        if exempt and not isinstance(reason, str) or exempt and not reason.strip():
            raise ValueError(f"exempt registry entry {index} needs a reason")
        if not exempt and reason is not None:
            raise ValueError(f"non-exempt registry entry {index} cannot have a reason")
        entries.append(
            CoverageEntry(
                service=service,
                method=method,
                path=path_value,
                tiers=tiers,
                exempt=exempt,
                reason=reason,
            )
        )
    return tuple(entries)


def build_coverage_report(
    client: httpx.Client,
    *,
    web_base_url: str,
    monitor_base_url: str,
    observed: Iterable[tuple[str, str, str]],
    registry_path: Path = REGISTRY_PATH,
) -> CoverageReport:
    _web_document, web_operations = fetch_openapi(
        client, service="web", base_url=web_base_url
    )
    _monitor_document, monitor_operations = fetch_openapi(
        client, service="monitor", base_url=monitor_base_url
    )
    operations = tuple([*web_operations, *monitor_operations])
    registry = load_registry(registry_path)
    live_keys = [operation.key for operation in operations]
    registry_keys = [entry.key for entry in registry]
    observed_requests = tuple(observed)

    covered = tuple(
        operation.key
        for operation in operations
        if any(
            service == operation.service
            and operation_matches(operation, method=method, path=path)
            for service, method, path in observed_requests
        )
    )
    exempt = tuple(
        operation.key
        for operation in operations
        if any(entry.key == operation.key and entry.exempt for entry in registry)
    )
    full_entries = {entry.key for entry in registry if "full" in entry.tiers and not entry.exempt}
    non_exempt = set(live_keys) - set(exempt)
    return CoverageReport(
        operations=operations,
        registry=registry,
        duplicate_registry=tuple(sorted(_duplicates(registry_keys))),
        unclassified=tuple(sorted(set(live_keys) - set(registry_keys))),
        stale=tuple(sorted(set(registry_keys) - set(live_keys))),
        duplicate_openapi=tuple(sorted(_duplicates(live_keys))),
        covered=tuple(sorted(set(covered))),
        exempt=tuple(sorted(set(exempt))),
        missing_full=tuple(sorted(non_exempt - full_entries)),
        uncovered=tuple(sorted(non_exempt - set(covered))),
    )


def _duplicates(keys: Iterable[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    seen: set[tuple[str, str, str]] = set()
    duplicates: set[tuple[str, str, str]] = set()
    for key in keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def format_coverage_report(report: CoverageReport) -> str:
    non_exempt = len(report.non_exempt_operations)
    covered_non_exempt = len(set(report.covered) & set(report.non_exempt_operations))
    web_count = sum(operation.service == "web" for operation in report.operations)
    monitor_count = sum(operation.service == "monitor" for operation in report.operations)
    lines = [
        "API FULL HTTP REACHABILITY",
        f"Web operations: {web_count}",
        f"Monitor operations: {monitor_count}",
        f"Total OpenAPI operations: {len(report.operations)}",
        f"Covered: {len(report.covered)}",
        f"Exempt: {len(report.exempt)}",
        f"Unclassified: {len(report.unclassified)}",
        f"Stale registry entries: {len(report.stale)}",
        f"HTTP reachability coverage: {covered_non_exempt} / {non_exempt} non-exempt operations",
        "Semantics: each covered operation received at least one observed real HTTP request; this is not full behavior/schema coverage.",
    ]
    for failure in report.failures:
        lines.append(f"FAIL: {failure}")
    return "\n".join(lines)


def _format_keys(keys: Iterable[tuple[str, str, str]]) -> str:
    return "; ".join(f"{service} {method} {path}" for service, method, path in keys)
