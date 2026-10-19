"""Repeatable T07 load runner for a provisioned non-production deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect


ROOT = Path(__file__).resolve().parents[2]
ERROR_MODES = ("http_401", "http_429", "http_503", "timeout")
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
PROVIDER_ERROR_KINDS = {
    "http_401": "upstream_auth_failed",
    "http_429": "upstream_rate_limited",
    "http_503": "upstream_overloaded",
    "timeout": "upstream_timeout",
}
BASELINE_PRODUCT_COMMIT = "8e83a7296a534504f6f5ef4320046d734412142b"


class LoadTestError(RuntimeError):
    pass


def provider_case_failures(mode: str, result: dict[str, Any]) -> list[str]:
    failures = []
    if result.get("submit_status") != 202:
        failures.append(f"{mode}: Turn submission was not HTTP 202")
    if result.get("state") != "failed":
        failures.append(f"{mode}: expected failed terminal, got {result.get('state')}")
    if result.get("error_kind") != PROVIDER_ERROR_KINDS[mode]:
        failures.append(f"{mode}: incorrect Provider error classification: {result.get('error_kind')}")
    if result.get("terminal_event_count") != 1 or result.get("event_sequence_gap"):
        failures.append(f"{mode}: missing/duplicate terminal event or event sequence gap")
    if result.get("session_after_provider_error_status") != 200:
        failures.append(f"{mode}: browser auth session was lost")
    return failures


async def set_fake_provider_mode(control: httpx.AsyncClient, url: str, token: str, mode: str) -> dict[str, Any]:
    response = await control.post(
        f"{url}/__t07__/mode", headers={"X-T07-Control-Token": token}, json={"mode": mode}
    )
    try:
        payload = response.json()
    except ValueError as error:
        raise LoadTestError(f"fake Provider mode {mode} did not return JSON") from error
    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("mode") != mode:
        raise LoadTestError(f"fake Provider mode switch to {mode} failed (HTTP {response.status_code})")
    return payload


def validate_run_kind(args: argparse.Namespace, output: Path) -> None:
    run_kind = getattr(args, "run_kind", "regression")
    if output.name == "baseline.json" and run_kind != "baseline":
        raise LoadTestError("baseline.json requires --run-kind baseline on the isolated old version")
    if run_kind != "baseline":
        return
    reference = getattr(args, "baseline_ref", None)
    if not reference:
        raise LoadTestError("a pre-fix baseline requires --baseline-ref")
    expected = subprocess.run(["git", "rev-parse", "--verify", f"{reference}^{{commit}}"], cwd=ROOT, capture_output=True, text=True, check=False)
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    if expected.returncode or expected.stdout.strip() != actual.stdout.strip():
        raise LoadTestError("baseline checkout must be at the exact --baseline-ref commit")
    if expected.stdout.strip() != BASELINE_PRODUCT_COMMIT:
        raise LoadTestError("baseline ref must match the frozen pre-T07 product commit")
    product_paths = ["core", "gateway", "server", "configs", "migrations", "compose.yaml"]
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *product_paths], cwd=ROOT, capture_output=True, text=True, check=False)
    if dirty.returncode or dirty.stdout.strip():
        raise LoadTestError("pre-fix baseline requires unchanged product code at --baseline-ref")


def validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise LoadTestError("--base-url must be an absolute http(s) URL")
    if any(marker in hostname for marker in ("prod", "production", "live")):
        raise LoadTestError("production-looking hostnames are not permitted")
    if parsed.username or parsed.password:
        raise LoadTestError("credentials must not be embedded in --base-url")
    return base_url.rstrip("/")


def validate_output_path(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve()
    allowed_root = (ROOT / "artifacts" / "load-tests" / "t07").resolve()
    if not resolved.is_relative_to(allowed_root):
        raise LoadTestError("--output must be inside artifacts/load-tests/t07")
    if resolved.exists():
        raise LoadTestError(f"refusing to overwrite existing report: {resolved}")
    if resolved.name == "baseline.json" and resolved.with_name("baseline-summary.md").exists():
        raise LoadTestError("refusing to overwrite existing baseline-summary.md")
    return resolved


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return round(ordered[index], 3)


def _problem_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if code:
        return str(code)
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    return None


def _profile_users(profile: dict[str, Any]) -> list[dict[str, str]]:
    password_env = str(profile.get("password_env") or "")
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        raise LoadTestError(f"required test password environment variable is missing: {password_env}")
    explicit = profile.get("users")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise LoadTestError("profile.users must be a non-empty list")
        users = []
        for item in explicit:
            if not isinstance(item, dict) or not item.get("username"):
                raise LoadTestError("each profile user requires a username")
            item_password_env = str(item.get("password_env") or password_env)
            item_password = os.environ.get(item_password_env, "")
            if not item_password:
                raise LoadTestError(f"required test password environment variable is missing: {item_password_env}")
            users.append({"username": str(item["username"]), "password": item_password})
        return users
    count = int(profile.get("user_count", 0))
    username_format = str(profile.get("username_format") or "")
    if count < 1 or not username_format:
        raise LoadTestError("profile requires user_count and username_format")
    return [
        {"username": username_format.format(index=index), "password": password}
        for index in range(1, count + 1)
    ]


async def _check_target(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get("/health/ready", timeout=5)
    except httpx.HTTPError as error:
        raise LoadTestError(f"test target health check failed: {error}") from error
    if response.status_code != 200:
        raise LoadTestError(f"test target is not ready: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise LoadTestError("/health/ready did not return JSON") from error
    environment = str(payload.get("deployment_environment", "")).lower()
    if environment not in {"test", "testing", "staging"}:
        raise LoadTestError(
            "refusing load: /health/ready must identify deployment_environment as test or staging"
        )
    return payload


async def _fake_provider_control(profile: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    url_env = str(profile.get("fake_provider_control_url_env") or "")
    token_env = str(profile.get("fake_provider_control_token_env") or "")
    control_url = os.environ.get(url_env, "").rstrip("/") if url_env else ""
    token = os.environ.get(token_env, "") if token_env else ""
    if not control_url or not token:
        raise LoadTestError("fake Provider control URL/token are required for all load scenarios")
    parsed = urlsplit(control_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LoadTestError("fake Provider control endpoint must use loopback HTTP")
    headers = {"X-T07-Control-Token": token}
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as control:
        response = await control.get(f"{control_url}/__t07__/health", headers=headers)
    if response.status_code != 200 or not isinstance(response.json(), dict):
        raise LoadTestError("fake Provider control endpoint is unavailable or unauthorized")
    return control_url, token, response.json()


async def _login_user(
    base_url: str,
    origin: str,
    user: dict[str, str],
    index: int,
    ramp_seconds: float,
    start_at: float,
    total_users: int = 50,
) -> dict[str, Any]:
    await asyncio.sleep(max(0.0, start_at + ramp_seconds * index / max(1, total_users) - time.monotonic()))
    client = httpx.AsyncClient(base_url=base_url, timeout=20, follow_redirects=False)
    started = time.monotonic()
    try:
        response = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": origin},
            json={"username": user["username"], "password": user["password"]},
        )
        login_ms = (time.monotonic() - started) * 1000
        login_payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 200:
            await client.aclose()
            return {"index": index, "username": user["username"], "login_status": response.status_code, "login_code": _problem_code(response), "login_ms": round(login_ms, 3), "client": None}
        session_started = time.monotonic()
        session_response = await client.get("/api/v1/auth/session", headers={"Origin": origin})
        session_ms = (time.monotonic() - session_started) * 1000
        if session_response.status_code != 200:
            await client.aclose()
            return {"index": index, "username": user["username"], "login_status": 200, "login_ms": round(login_ms, 3), "session_status": session_response.status_code, "session_code": _problem_code(session_response), "session_ms": round(session_ms, 3), "client": None}
        session_payload = session_response.json()
        csrf = str(session_payload.get("csrf_token") or login_payload.get("csrf_token") or "")
        workspaces = login_payload.get("workspace_ids") or session_payload.get("workspace_ids") or []
        if not csrf or not workspaces:
            await client.aclose()
            return {"index": index, "username": user["username"], "login_status": 200, "login_ms": round(login_ms, 3), "session_status": 200, "session_ms": round(session_ms, 3), "identity_error": "missing csrf/workspace", "client": None}
        create_started = time.monotonic()
        created = await client.post(
            "/api/v1/sessions",
            headers={"Origin": origin, "X-CSRF-Token": csrf},
            json={"workspace_id": str(workspaces[0])},
        )
        if created.status_code != 201:
            await client.aclose()
            return {"index": index, "username": user["username"], "login_status": 200, "login_ms": round(login_ms, 3), "session_status": 200, "session_ms": round(session_ms, 3), "conversation_status": created.status_code, "conversation_code": _problem_code(created), "conversation_ms": round((time.monotonic() - create_started) * 1000, 3), "client": None}
        return {
            "index": index,
            "username": user["username"],
            "user_id": str(session_payload.get("user_id") or login_payload.get("user_id") or ""),
            "workspace_id": str(workspaces[0]),
            "session_id": str(created.json().get("session_id") or ""),
            "csrf": csrf,
            "login_status": 200,
            "login_ms": round(login_ms, 3),
            "session_status": 200,
            "session_ms": round(session_ms, 3),
            "conversation_status": 201,
            "conversation_ms": round((time.monotonic() - create_started) * 1000, 3),
            "last_http_completed_at": time.monotonic(),
            "client": client,
        }
    except Exception:
        await client.aclose()
        raise


async def _open_websocket(user: dict[str, Any], *, base_url: str, origin: str) -> tuple[ClientConnection | None, dict[str, Any]]:
    client = user.get("client")
    if client is None:
        return None, {"username": user["username"], "status": "not_connected", "reason": "login_failed"}
    started = time.monotonic()
    stage, websocket, handed_off = "ticket_request", None, False
    metrics = {"username": user["username"]}
    ticket_trace = []

    async def observe_ticket(event, details):
        # Fixed httpcore event names only; never retain URLs, headers or details.
        if event in {"connection.connect_tcp.started", "connection.connect_tcp.complete",
                     "connection.start_tls.started", "connection.start_tls.complete",
                     "http11.send_request_headers.started", "http11.send_request_headers.complete",
                     "http11.receive_response_headers.started", "http11.receive_response_headers.complete",
                     "http11.receive_response_headers.failed"} and len(ticket_trace) < 20:
            ticket_trace.append(event)

    try:
        ticket = await client.post("/api/v1/auth/ws-ticket", headers={"Origin": origin, "X-CSRF-Token": user["csrf"]},
                                   extensions={"trace": observe_ticket})
        stage = "ticket_response"
        if ticket.status_code != 200:
            metrics.update(status=ticket.status_code, stage=stage, code=_problem_code(ticket))
            return None, metrics
        cookie = client.cookies.get("nlp_session")
        if not cookie:
            metrics.update(status="missing_cookie", stage=stage)
            return None, metrics
        scheme = "wss" if base_url.startswith("https://") else "ws"
        ws_url = f"{scheme}://{urlsplit(base_url).netloc}/ws/v1?ticket={ticket.json()['ticket']}"
        stage = "websocket_upgrade"
        websocket = await connect(
            ws_url, origin=origin, additional_headers={"Cookie": f"nlp_session={cookie}"},
            proxy=None, open_timeout=10, close_timeout=5,
        )
        stage = "connection_ready"
        ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        if ready.get("type") != "connection.ready":
            metrics.update(status="not_ready", stage=stage, frame_type=ready.get("type"))
            return None, metrics
        handed_off = True
        metrics.update(status="ready", latency_ms=round((time.monotonic() - started) * 1000, 3))
        return websocket, metrics
    except Exception as error:
        # A ticket HTTP transport failure is not an upgraded WebSocket failure.
        # Keep the failure, classify its stage, and do not retry or log secrets.
        metrics.update(status="error", stage=stage, error=type(error).__name__)
        if stage == "ticket_request":
            metrics.update(ticket_trace=ticket_trace,
                           ticket_connection_reused=bool(ticket_trace) and "connection.connect_tcp.started" not in ticket_trace,
                           idle_before_ticket_ms=round((started - user.get("last_http_completed_at", started)) * 1000, 3))
        return None, metrics
    finally:
        if websocket is not None and not handed_off:
            try:
                await websocket.close()
            except Exception:
                metrics["cleanup_failed"] = True


async def _submit_and_wait(
    user: dict[str, Any],
    *,
    run_id: str,
    timeout_s: float,
    ramp_seconds: float,
    index: int,
    total: int,
    start_at: float,
    model_profile: str | None = None,
) -> dict[str, Any]:
    client: httpx.AsyncClient = user["client"]
    await asyncio.sleep(max(0.0, start_at + ramp_seconds * index / max(1, total) - time.monotonic()))
    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()
    response = await client.post(
        "/api/v1/chat/turns",
        headers={"Origin": user["origin"], "X-CSRF-Token": user["csrf"]},
        json={
            "session_id": user["session_id"],
            "content": f"T07 load test {run_id} user {user['index']}",
            "idempotency_key": f"t07-{run_id}-{user['index']}",
            **({"model_profile": model_profile} if model_profile else {}),
        },
    )
    accepted_ms = round((time.monotonic() - started) * 1000, 3)
    result: dict[str, Any] = {
        "username": user["username"],
        "submit_status": response.status_code,
        "submit_code": _problem_code(response),
        "submit_ms": accepted_ms,
        "started_at": started_wall.isoformat(),
    }
    if response.status_code != 202:
        return result
    accepted = response.json()
    turn_id = str(accepted["turn_id"])
    result["turn_id"] = turn_id
    result["accepted_status"] = accepted.get("status")
    result["accepted_at_ms"] = round((time.monotonic() - started) * 1000, 3)
    if accepted.get("status") == "accepted":
        result["queued_at_ms"] = result["accepted_at_ms"]
    deadline = time.monotonic() + timeout_s
    turn_payload: dict[str, Any] = {}
    running_at_ms: float | None = None
    while time.monotonic() < deadline:
        turn_response = await client.get(f"/api/v1/chat/turns/{turn_id}")
        if turn_response.status_code != 200:
            result["poll_status"] = turn_response.status_code
            result["poll_code"] = _problem_code(turn_response)
            return result
        turn_payload = turn_response.json()
        result["state"] = turn_payload.get("status")
        if result["state"] == "running" and running_at_ms is None:
            running_at_ms = round((time.monotonic() - started) * 1000, 3)
            result["running_at_ms"] = running_at_ms
        if str(result["state"]) in TERMINAL:
            break
        await asyncio.sleep(0.2)
    if str(turn_payload.get("status")) not in TERMINAL:
        result["state"] = "timeout"
        return result
    result["terminal_ms"] = round((time.monotonic() - started) * 1000, 3)
    result["error_kind"] = turn_payload.get("error_kind")
    result["error_message"] = turn_payload.get("error_message")
    result["final_text"] = str(turn_payload.get("final_text") or "")[:120]
    events, events_status = await collect_terminal_events(client, turn_id)
    if events_status == 200:
        sequences = [int(item.get("sequence", 0)) for item in events]
        result["event_sequence_gap"] = sequences != list(range(1, len(sequences) + 1))
        terminal_events = [
            item.get("type")
            for item in events
            if item.get("type") in {"turn.completed", "turn.failed", "turn.cancelled"}
        ]
        result["terminal_event_count"] = len(terminal_events)
        first_token = next((item for item in events if item.get("type") == "message.delta"), None)
        result["first_token_event_at"] = first_token.get("created_at") if first_token else None
        if first_token and first_token.get("created_at"):
            try:
                first_token_at = datetime.fromisoformat(
                    str(first_token["created_at"]).replace("Z", "+00:00")
                )
                # Persistence timestamps are UTC; MySQL may serialize them
                # without an offset. Preserve explicit offsets when present.
                if first_token_at.tzinfo is None:
                    first_token_at = first_token_at.replace(tzinfo=timezone.utc)
                result["first_token_ms"] = round(
                    (first_token_at - started_wall).total_seconds() * 1000, 3
                )
            except ValueError:
                result["first_token_ms"] = None
    else:
        result["events_status"] = events_status
    return result


async def collect_terminal_events(client: httpx.AsyncClient, turn_id: str, *, timeout_s: float = 5) -> tuple[list[dict[str, Any]], int]:
    """Drain every replay page, allowing the old baseline's terminal-event lag."""
    events: list[dict[str, Any]] = []
    after_sequence = 0
    deadline = time.monotonic() + timeout_s
    while True:
        response = await client.get(f"/api/v1/chat/turns/{turn_id}/events", params={"after_sequence": after_sequence, "limit": 500})
        if response.status_code != 200:
            return events, response.status_code
        page = response.json().get("items", [])
        if not isinstance(page, list):
            raise LoadTestError("event replay did not return an items list")
        events.extend(page)
        if page:
            next_sequence = int(page[-1]["sequence"])
            if next_sequence <= after_sequence:
                raise LoadTestError("event replay pagination made no progress")
            after_sequence = next_sequence
        terminal = any(event.get("type") in {"turn.completed", "turn.failed", "turn.cancelled"} for event in events)
        if len(page) == 500:
            if time.monotonic() >= deadline:
                raise LoadTestError("event replay pagination exceeded its deadline")
            continue
        if terminal or time.monotonic() >= deadline:
            return events, 200
        await asyncio.sleep(0.1)


def validate_workload_size(profile, args, base_url):
    size = int(profile.get("user_count", 0)), int(profile.get("turn_count", 0))
    comparison = getattr(args, "isolated_three_class_comparison", False)
    if size == (50, 30) and not comparison:
        return
    target = urlsplit(base_url)
    if (size == (120, 30) and comparison and profile.get("classroom_count") == 3
            and getattr(args, "run_kind", "regression") == "regression"
            and target.scheme == "http" and target.hostname == "127.0.0.1"):
        return
    raise LoadTestError("release profile requires 50 users/30 Turns; three-class comparison requires explicit isolated loopback mode")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = validate_base_url(args.base_url)
    output = validate_output_path(args.output)
    validate_run_kind(args, output)
    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = ROOT / profile_path
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("version") != 1:
        raise LoadTestError("unsupported profile version")
    requested_scenarios = profile.get("scenarios", [])
    supported_scenarios = {"auth_burst", "turn_burst", "provider_errors"}
    if not isinstance(requested_scenarios, list) or any(
        not isinstance(item, str) for item in requested_scenarios
    ):
        raise LoadTestError("profile.scenarios must be a list of scenario names")
    if not set(requested_scenarios) <= supported_scenarios:
        missing = sorted(set(requested_scenarios) - supported_scenarios)
        raise LoadTestError(
            "profile requests scenarios not implemented by this runner: "
            + ", ".join(missing)
        )
    validate_workload_size(profile, args, base_url)
    users = _profile_users(profile)
    if len(users) < int(profile.get("turn_count", 0)):
        raise LoadTestError("turn_count cannot exceed the number of provisioned users")
    timeout_s = float(profile.get("turn_timeout_seconds", 240))
    run_id = uuid.uuid4().hex[:12]
    origin = base_url
    started_at = datetime.now(timezone.utc).isoformat()
    phases: dict[str, Any] = {}
    user_results: list[dict[str, Any]] = []
    websockets: list[ClientConnection] = []
    failures: list[str] = []
    http = httpx.AsyncClient(base_url=base_url, timeout=20, follow_redirects=False)
    try:
        health = await _check_target(http)
        if getattr(args, "run_kind", "regression") == "baseline" and health.get("baseline_product_commit") != BASELINE_PRODUCT_COMMIT:
            raise LoadTestError("baseline target product commit does not match the frozen pre-fix commit")
        control_url, control_token, fake_before = await _fake_provider_control(profile)
        login_started = time.monotonic()
        login_started_at = datetime.now(timezone.utc).isoformat()
        ramp = float(profile.get("login_ramp_seconds", 10))
        user_results = await asyncio.gather(*(
            _login_user(base_url, origin, user, index, ramp, login_started, len(users))
            for index, user in enumerate(users)
        ))
        phases["auth_burst"] = {
            "started_at": login_started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ramp_seconds": ramp,
            "login_p95_ms": _percentile([float(item["login_ms"]) for item in user_results if item.get("login_ms") is not None], 0.95),
            "session_p95_ms": _percentile([float(item["session_ms"]) for item in user_results if item.get("session_ms") is not None], 0.95),
            "login_successes": sum(item.get("login_status") == 200 for item in user_results),
            "correct_credential_401": sum(item.get("login_status") == 401 for item in user_results),
        }
        for item in user_results:
            item["origin"] = origin
            if item.get("login_status") != 200 or item.get("session_status") != 200 or not item.get("session_id"):
                failures.append(f"auth/session setup failed for user index {item['index']}")

        ws_started = time.monotonic()
        ws_started_at = datetime.now(timezone.utc).isoformat()
        ws_results = await asyncio.gather(*(
            _open_websocket(item, base_url=base_url, origin=origin)
            for item in user_results
        ), return_exceptions=True)
        ws_metrics = []
        for result in ws_results:
            if isinstance(result, Exception):
                ws_metrics.append({"status": "error", "error": type(result).__name__})
                failures.append("WebSocket handshake failed")
                continue
            websocket, metrics = result
            ws_metrics.append(metrics)
            if websocket is not None:
                websockets.append(websocket)
            else:
                failures.append("WebSocket did not become ready")
        phases["websocket"] = {
            "started_at": ws_started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "connected": len(websockets),
            "requested": len(users),
            "setup_seconds": round(time.monotonic() - ws_started, 3),
            "ready_p95_ms": _percentile([float(item["latency_ms"]) for item in ws_metrics if item.get("latency_ms") is not None], 0.95),
        }

        turn_count = int(profile.get("turn_count", 30))
        turn_ramp = float(profile.get("turn_ramp_seconds", 10))
        turn_started = time.monotonic()
        turn_started_at = datetime.now(timezone.utc).isoformat()
        turns = await asyncio.gather(*(
            _submit_and_wait(
                user_results[index],
                run_id=run_id,
                timeout_s=timeout_s,
                ramp_seconds=turn_ramp,
                index=index,
                total=turn_count,
                start_at=turn_started,
            )
            for index in range(turn_count)
            if user_results[index].get("client") is not None
        ), return_exceptions=True)
        turn_results = [
            {"error": type(item).__name__} if isinstance(item, Exception) else item
            for item in turns
        ]
        phases["turn_burst"] = {
            "started_at": turn_started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "turn_count": turn_count,
            "ramp_seconds": turn_ramp,
            "submit_p95_ms": _percentile([float(item["submit_ms"]) for item in turn_results if item.get("submit_ms") is not None], 0.95),
            "submit_p99_ms": _percentile([float(item["submit_ms"]) for item in turn_results if item.get("submit_ms") is not None], 0.99),
            "completed": sum(item.get("state") == "completed" for item in turn_results),
            "terminal_failures": sum(item.get("state") in {"failed", "interrupted"} for item in turn_results),
            "timeouts": sum(item.get("state") == "timeout" for item in turn_results),
            "http_status_counts": {
                str(code): sum(item.get("submit_status") == code for item in turn_results)
                for code in sorted({item.get("submit_status") for item in turn_results if item.get("submit_status") is not None})
            },
            "problem_code_counts": {
                str(code): sum(item.get("submit_code") == code for item in turn_results)
                for code in sorted({item.get("submit_code") for item in turn_results if item.get("submit_code")})
            },
            "duplicate_terminal_events": sum(int(item.get("terminal_event_count", 0)) > 1 for item in turn_results),
            "missing_terminal_events": sum(int(item.get("terminal_event_count", 0)) == 0 for item in turn_results),
            "sequence_gaps": sum(bool(item.get("event_sequence_gap")) for item in turn_results),
            "queue_wait_p95_ms": _percentile([
                max(0.0, float(item["running_at_ms"]) - float(item["accepted_at_ms"]))
                for item in turn_results if item.get("running_at_ms") is not None
            ], 0.95),
            "first_token_p95_ms": _percentile([
                float(item["first_token_ms"])
                for item in turn_results if item.get("first_token_ms") is not None
            ], 0.95),
            "turns": turn_results,
        }
        if phases["turn_burst"]["completed"] != turn_count:
            failures.append("not all fake-model Turns completed")
        if phases["turn_burst"]["submit_p95_ms"] is None or phases["turn_burst"]["submit_p95_ms"] > 2_000:
            failures.append("Turn submission p95 exceeded the 2 second SLO")
        if phases["turn_burst"]["submit_p99_ms"] is None or phases["turn_burst"]["submit_p99_ms"] > 5_000:
            failures.append("Turn submission p99 exceeded the 5 second SLO")
        if phases["turn_burst"]["queue_wait_p95_ms"] is None or phases["turn_burst"]["queue_wait_p95_ms"] > 120_000:
            failures.append("queue wait p95 exceeded the 120 second SLO")
        if any(item.get("submit_status") != 202 for item in turn_results):
            failures.append("one or more valid Turn submissions were not accepted with HTTP 202")
        if phases["turn_burst"]["duplicate_terminal_events"] or phases["turn_burst"]["missing_terminal_events"] or phases["turn_burst"]["sequence_gaps"]:
            failures.append("turn event integrity check failed")

        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as control:
            error_results = []
            try:
                for index, mode in enumerate(ERROR_MODES):
                    try:
                        switched = await set_fake_provider_mode(control, control_url, control_token, mode)
                        test_user = user_results[index]
                        if test_user.get("client") is None:
                            raise LoadTestError("Provider scenario has no authenticated virtual user")
                        result = await _submit_and_wait(
                            test_user, run_id=f"{run_id}-provider-{mode}",
                            timeout_s=max(timeout_s, 90), ramp_seconds=0,
                            index=0, total=1, start_at=time.monotonic(),
                            model_profile="qwen" if mode == "timeout" else None,
                        )
                        session_response = await test_user["client"].get(
                            "/api/v1/auth/session", headers={"Origin": origin}
                        )
                        result["session_after_provider_error_status"] = session_response.status_code
                        verified = await control.get(f"{control_url}/__t07__/health", headers={"X-T07-Control-Token": control_token})
                        verified.raise_for_status()
                        if int(verified.json().get("calls_total", 0)) <= int(switched.get("calls_total", 0)):
                            failures.append(f"{mode}: fake Provider received no call for this scenario")
                        failures.extend(provider_case_failures(mode, result))
                        error_results.append({"mode": mode, **result})
                    except (LoadTestError, httpx.HTTPError, ValueError) as error:
                        failures.append(f"{mode}: {error}")
                        error_results.append({"mode": mode, "scenario_error": str(error)})
            finally:
                try:
                    await set_fake_provider_mode(control, control_url, control_token, "success")
                except (LoadTestError, httpx.HTTPError) as error:
                    failures.append(f"fake Provider reset failed: {error}")
        phases["provider_errors"] = {"cases": error_results}
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as control:
            fake_after_response = await control.get(
                f"{control_url}/__t07__/health",
                headers={"X-T07-Control-Token": control_token},
            )
        fake_after = fake_after_response.json()
        if int(fake_after.get("calls_total", 0)) <= int(fake_before.get("calls_total", 0)):
            failures.append("fake Provider received no model requests; target may not be configured for the fake")
        if phases["auth_burst"]["login_successes"] != len(users):
            failures.append("not all virtual users logged in")
        if phases["auth_burst"]["correct_credential_401"]:
            failures.append("a valid virtual user received HTTP 401")

        return {
            "schema_version": 1,
            "run_kind": getattr(args, "run_kind", "regression"),
            "baseline_ref": getattr(args, "baseline_ref", None),
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "result": "failed" if failures else "passed",
            "failures": failures,
            "target": {"origin": origin, "deployment_environment": health.get("deployment_environment"), "baseline_product_commit": health.get("baseline_product_commit")},
            "machine": {"platform": platform.platform(), "processor": platform.processor(), "python": sys.version.split()[0]},
            "source": {"git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()},
            "deployment_profile": {key: profile.get(key) for key in ("user_count", "classroom_count", "turn_count", "worker_replicas", "worker_concurrency", "database_pool_size", "database_max_overflow", "database_sync_pool_size", "database_sync_max_overflow", "worker_database_pool_size", "worker_database_max_overflow", "worker_async_engine_count", "worker_sync_pool_size", "worker_sync_max_overflow", "database_pool_timeout_seconds", "expected_fake_model_delay_seconds")},
            "scenario_coverage": requested_scenarios,
            "fake_provider": {"calls_before": fake_before.get("calls_total"), "calls_after": fake_after.get("calls_total"), "configured_delay_seconds": profile.get("expected_fake_model_delay_seconds")},
            "phases": phases,
            "virtual_users": [{key: value for key, value in item.items() if key not in {"client", "csrf"}} for item in user_results],
            "websockets": ws_metrics,
        }
    finally:
        for websocket in websockets:
            await websocket.close()
        for user in user_results:
            client = user.get("client")
            if client is not None:
                await client.aclose()
        await http.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-kind", choices=("baseline", "regression"), default="regression")
    parser.add_argument("--baseline-ref", help="exact old-version commit, required for baseline runs")
    parser.add_argument("--isolated-three-class-comparison", action="store_true", help="bounded 120-user comparison on disposable loopback services only")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except (LoadTestError, OSError, ValueError, httpx.HTTPError) as error:
        print(f"T07 load test refused/failed: {error}", file=sys.stderr)
        return 2
    output = validate_output_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as report_file:
            report_file.write(json.dumps(report, ensure_ascii=False, indent=2))
    except FileExistsError as error:
        print(f"T07 load test refused to overwrite report: {output}", file=sys.stderr)
        return 2
    if output.name == "baseline.json":
        summary = output.with_name("baseline-summary.md")
        auth = report.get("phases", {}).get("auth_burst", {})
        turns = report.get("phases", {}).get("turn_burst", {})
        summary_text = "\n".join(
            [
                "# T07 修复前负载基线",
                "",
                f"- 结果：`{report.get('result')}`",
                f"- 运行 ID：`{report.get('run_id')}`",
                f"- 时间：`{report.get('started_at')}` — `{report.get('finished_at')}`",
                f"- Git commit：`{report.get('source', {}).get('git_commit')}`",
                f"- 部署环境：`{report.get('target', {}).get('deployment_environment')}`",
                f"- Worker：{report.get('deployment_profile', {}).get('worker_replicas')} 副本 × {report.get('deployment_profile', {}).get('worker_concurrency')} 并发/副本",
                f"- DB pool：size={report.get('deployment_profile', {}).get('database_pool_size')}, overflow={report.get('deployment_profile', {}).get('database_max_overflow')}, timeout={report.get('deployment_profile', {}).get('database_pool_timeout_seconds')}s",
                f"- Worker DB pool：size={report.get('deployment_profile', {}).get('worker_database_pool_size')}, overflow={report.get('deployment_profile', {}).get('worker_database_max_overflow')}",
                f"- 假模型配置延迟：{report.get('fake_provider', {}).get('configured_delay_seconds')}s",
                f"- 实际执行场景：{', '.join(report.get('scenario_coverage', []))}",
                "",
                "## 关键结果",
                "",
                f"- 登录成功：{auth.get('login_successes')}; 有效凭据 401：{auth.get('correct_credential_401')}",
                f"- 登录 p95：{auth.get('login_p95_ms')} ms; session p95：{auth.get('session_p95_ms')} ms",
                f"- Turn：{turns.get('turn_count')} 个; 完成：{turns.get('completed')}; 失败：{turns.get('terminal_failures')}; 超时：{turns.get('timeouts')}",
                f"- 提交 p95/p99：{turns.get('submit_p95_ms')} / {turns.get('submit_p99_ms')} ms; 排队等待 p95：{turns.get('queue_wait_p95_ms')} ms",
                f"- 失败说明：{'; '.join(report.get('failures') or ['无'])}",
                "",
                "该摘要由一次真实负载执行生成；机器可读原始结果见同目录 `baseline.json`。未执行的场景不会被推断或补造。",
                "",
            ]
        )
        try:
            with summary.open("x", encoding="utf-8") as summary_file:
                summary_file.write(summary_text)
        except FileExistsError:
            print(f"T07 load test refused to overwrite summary: {summary}", file=sys.stderr)
            return 2
    print(f"T07 load report: {output}")
    print(f"Result: {report['result']}; failures: {len(report['failures'])}")
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
