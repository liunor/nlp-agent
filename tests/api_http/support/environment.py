"""Managed, isolated infrastructure for real HTTP API tests.

The lifecycle deliberately uses ephemeral Docker containers for MySQL and
Redis and local uvicorn processes for the two FastAPI applications. It does
not use the repository Compose project, so a developer's existing volumes or
services cannot be selected accidentally.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MYSQL_IMAGE = "mysql:8.4"
REDIS_IMAGE = "redis:7.4-alpine"
TEST_PASSWORD = "ApiHttp-password-123!"
DIAGNOSTIC_LOG_NAMES = ("web.log", "monitor.log", "worker.log")
SENSITIVE_JSON_KEY_PARTS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "api_key",
    "token",
)


class ApiHttpEnvironmentError(RuntimeError):
    """Raised when the isolated HTTP environment cannot be made safely."""


@dataclass(frozen=True, slots=True)
class SeededUser:
    username: str
    password: str
    user_id: str
    workspace_id: str
    role: str


@dataclass
class ManagedHttpEnvironment:
    """Handles all resources created for one pytest session."""

    env: dict[str, str]
    web_port: int
    monitor_port: int
    mysql_port: int
    redis_port: int
    redis_db: int
    redis_task_stream: str
    redis_task_group: str
    mysql_container: str
    redis_container: str
    log_dir: Path
    uploads_root: Path
    processes: list[subprocess.Popen[str]]
    log_handles: list[Any]
    users: dict[str, SeededUser]
    observed_http_operations: set[tuple[str, str, str]] = field(default_factory=set)
    observed_websocket_routes: set[str] = field(default_factory=set)
    worker_process: subprocess.Popen[str] | None = None

    @property
    def web_base_url(self) -> str:
        return f"http://127.0.0.1:{self.web_port}"

    @property
    def monitor_base_url(self) -> str:
        return f"http://127.0.0.1:{self.monitor_port}"

    @property
    def web_origin(self) -> str:
        return self.web_base_url

    @classmethod
    def start(cls) -> "ManagedHttpEnvironment":
        _assert_safe_parent_environment()
        _require_docker_daemon()

        web_port = _free_port()
        monitor_port = _free_port()
        mysql_port = _free_port()
        redis_port = _free_port()
        run_id = uuid.uuid4().hex[:12]
        mysql_container = f"pro-nlp-api-http-mysql-{run_id}"
        redis_container = f"pro-nlp-api-http-redis-{run_id}"
        database = f"nlp_agent_api_http_{run_id}"
        redis_db = 15
        redis_task_stream = "nlp-agent:turns"
        redis_task_group = "nlp-agent-workers"
        log_dir = Path(tempfile.mkdtemp(prefix="pro_nlp_api_http_"))
        uploads_root = log_dir / "uploads"
        environment = cls(
            env={},
            web_port=web_port,
            monitor_port=monitor_port,
            mysql_port=mysql_port,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_task_stream=redis_task_stream,
            redis_task_group=redis_task_group,
            mysql_container=mysql_container,
            redis_container=redis_container,
            log_dir=log_dir,
            uploads_root=uploads_root,
            processes=[],
            log_handles=[],
            users={},
        )
        try:
            environment.env = _safe_child_environment(
                database=database,
                redis_db=redis_db,
                web_port=web_port,
                monitor_port=monitor_port,
                mysql_port=mysql_port,
                redis_port=redis_port,
                uploads_root=uploads_root,
            )
            _docker_run_mysql(mysql_container, mysql_port, database)
            _docker_run_redis(redis_container, redis_port)
            _wait_tcp(mysql_port, label="MySQL")
            _wait_tcp(redis_port, label="Redis")
            _verify_redis(redis_port, redis_db, run_id)
            _run_migrations(environment.env)
            environment.users = _seed_users(environment.env)
            environment._start_server(
                module="tests.api_http.support.web_entrypoint:app",
                port=web_port,
                log_name="web.log",
            )
            environment._start_server(
                # Production intentionally disables the public Monitor
                # OpenAPI route.  The test-only entrypoint exposes the same
                # schema on the isolated test process for inventory checks.
                module="tests.api_http.support.monitor_entrypoint:app",
                port=monitor_port,
                log_name="monitor.log",
            )
            environment._start_worker()
            _wait_http(f"{environment.web_base_url}/health/ready", label="Web")
            _wait_http(
                f"{environment.monitor_base_url}/health/ready",
                label="Monitor",
            )
            environment._wait_worker_ready()
            return environment
        except Exception:
            environment.close()
            raise

    def _start_server(self, *, module: str, port: int, log_name: str) -> None:
        log_handle = (self.log_dir / log_name).open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                module,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self.log_handles.append(log_handle)
        self.processes.append(process)

    def _start_worker(self) -> None:
        log_handle = (self.log_dir / "worker.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.api_http.support.worker_entrypoint",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self.log_handles.append(log_handle)
        self.processes.append(process)
        self.worker_process = process

    def _wait_worker_ready(self, *, timeout_s: float = 120.0) -> None:
        from redis import Redis

        if self.worker_process is None:
            raise ApiHttpEnvironmentError("Worker process was not started")
        client = Redis(
            host="127.0.0.1",
            port=self.redis_port,
            db=self.redis_db,
            decode_responses=True,
        )
        deadline = time.monotonic() + timeout_s
        last_error = ""
        try:
            while time.monotonic() < deadline:
                if self.worker_process.poll() is not None:
                    raise ApiHttpEnvironmentError(
                        "Worker exited during startup:\n" + self.diagnostics()
                    )
                try:
                    groups = client.xinfo_groups(self.redis_task_stream)
                    if any(
                        str(group.get("name")) == self.redis_task_group
                        for group in groups
                    ):
                        return
                except Exception as error:
                    last_error = str(error)
                time.sleep(1)
        finally:
            client.close()
        raise ApiHttpEnvironmentError(
            "Worker did not create its Redis consumer group in time"
            + (f": {last_error}" if last_error else "")
            + "\n"
            + self.diagnostics()
        )

    def close(self) -> None:
        self._print_live_inventory()
        for process in reversed(self.processes):
            if process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.processes.clear()
        if os.environ.get("API_HTTP_PRINT_DIAGNOSTICS") == "1":
            print(_redact_diagnostics(self.diagnostics()))
        self._copy_diagnostics()
        for container in (self.mysql_container, self.redis_container):
            subprocess.run(
                ["docker", "rm", "--force", container],
                cwd=ROOT,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        for handle in self.log_handles:
            handle.close()
        self.log_handles.clear()
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _copy_diagnostics(self) -> None:
        destination_value = os.environ.get("API_HTTP_ARTIFACT_DIR", "").strip()
        if not destination_value:
            return
        destination = Path(destination_value)
        try:
            destination.mkdir(parents=True, exist_ok=True)
            for log_name in DIAGNOSTIC_LOG_NAMES:
                log_path = self.log_dir / log_name
                if not log_path.is_file():
                    continue
                (destination / log_path.name).write_text(
                    _redact_diagnostics(log_path.read_text(encoding="utf-8", errors="replace")),
                    encoding="utf-8",
                )
            (destination / "environment-diagnostics.txt").write_text(
                _redact_diagnostics(self.diagnostics()),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"API HTTP diagnostics could not be copied: {error}")

    def record_http_operation(self, method: str, path: str, *, port: int | None) -> None:
        service = "monitor" if port == self.monitor_port else "web"
        self.observed_http_operations.add((service, method.upper(), path))

    def record_websocket_route(self, path: str) -> None:
        self.observed_websocket_routes.add(path)

    def _print_live_inventory(self) -> None:
        """Report the operations observed in this real HTTP pytest session."""
        if not self.processes:
            return
        try:
            import httpx

            from .inventory import fetch_openapi, operation_matches

            with httpx.Client(timeout=5) as client:
                _, web = fetch_openapi(
                    client, service="web", base_url=self.web_base_url
                )
                _, monitor = fetch_openapi(
                    client, service="monitor", base_url=self.monitor_base_url
                )
            all_operations = (*web, *monitor)
            inventory = {item.key for item in all_operations}
            covered = {
                operation.key
                for operation in all_operations
                if any(
                    service == operation.service
                    and operation_matches(operation, method=method, path=path)
                    for service, method, path in self.observed_http_operations
                )
            }
            missing = sorted(inventory - covered)
            print(
                "\nAPI HTTP reachability inventory (observed requests):\n"
                f"Web operations: {len(web)}\n"
                f"Monitor operations: {len(monitor)}\n"
                f"Total: {len(inventory)}\n"
                f"Reachable operations: {len(covered)}\n"
                f"Untested operations: {len(missing)}"
            )
            if missing:
                print("Untested operation keys:")
                for service, method, path in missing:
                    print(f"  {service} {method} {path}")
        except Exception as error:
            print(f"API HTTP inventory coverage unavailable during teardown: {error}")

    def diagnostics(self) -> str:
        chunks = []
        for log_name in DIAGNOSTIC_LOG_NAMES:
            log_path = self.log_dir / log_name
            if not log_path.is_file():
                continue
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = "<log unavailable>"
            chunks.append(f"--- {log_path.name} ---\n{content[-6000:]}")
        return "\n".join(chunks)


def _redact_diagnostics(value: str) -> str:
    """Keep service diagnostics useful without persisting request credentials."""

    redacted_lines: list[str] = []
    for line in value.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            pass
        else:
            content = json.dumps(
                _redact_json_value(parsed),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        redacted_lines.append(content + newline)
    value = "".join(redacted_lines)

    patterns = (
        (r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
        (r"(?i)(cookie\s*:\s*)[^\r\n]+", r"\1[REDACTED]"),
        (r"(?i)(x-csrf-token\s*[:=]\s*)[^\s]+", r"\1[REDACTED]"),
        (
            r"(?i)([\"']?(?:authorization|cookie|set-cookie|x-csrf-token|csrf[_-]?token|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^,\s}]+)",
            r"\1[REDACTED]",
        ),
        (r"(?i)(://[^/\s:@]+:)[^@\s]+(@)", r"\1[REDACTED]\2"),
    )
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value


def _redact_json_value(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_json_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {name: _redact_json_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _is_sensitive_json_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_JSON_KEY_PARTS)


def _assert_safe_parent_environment() -> None:
    """Abort rather than reuse an explicitly supplied non-test environment."""

    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "NLP_AGENT_DATABASE_URL",
        "NLP_AGENT_REDIS_URL",
        "MYSQL_URL",
        "API_HTTP_DATABASE_URL",
        "API_HTTP_REDIS_URL",
    ):
        if os.environ.get(key, "").strip():
            raise ApiHttpEnvironmentError(
                f"Refusing real API tests while {key} is set; managed tests create a fresh isolated service."
            )
    for key in (
        "WEB_BASE_URL",
        "MONITOR_BASE_URL",
        "NLP_AGENT_WEB_BASE_URL",
        "NLP_AGENT_MONITOR_BASE_URL",
        "NLP_AGENT_WEB_HOST",
        "NLP_AGENT_MONITOR_HOST",
        "API_HTTP_WEB_BASE_URL",
        "API_HTTP_MONITOR_BASE_URL",
    ):
        value = os.environ.get(key, "").strip()
        if value and not _is_loopback(value):
            raise ApiHttpEnvironmentError(
                f"Refusing real API tests for non-loopback {key}={value!r}."
            )


def _is_loopback(value: str) -> bool:
    if value in {"127.0.0.1", "localhost", "::1"}:
        return True
    if value.startswith(("http://127.0.0.1:", "http://localhost:", "https://127.0.0.1:")):
        return True
    return False


def _require_docker_daemon() -> None:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ApiHttpEnvironmentError(
            "Docker daemon is required for api_http tests; start Docker Desktop first."
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ApiHttpEnvironmentError(
            f"Docker daemon is unavailable; real HTTP tests aborted safely: {detail}"
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker_run_mysql(container: str, port: int, database: str) -> None:
    _docker_run(
        [
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--publish",
            f"127.0.0.1:{port}:3306",
            "--env",
            f"MYSQL_DATABASE={database}",
            "--env",
            "MYSQL_USER=api_http",
            "--env",
            "MYSQL_PASSWORD=api_http_password",
            "--env",
            "MYSQL_ROOT_PASSWORD=root_api_http_password",
            MYSQL_IMAGE,
        ]
    )


def _docker_run_redis(container: str, port: int) -> None:
    _docker_run(
        [
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--publish",
            f"127.0.0.1:{port}:6379",
            REDIS_IMAGE,
        ]
    )


def _docker_run(arguments: list[str]) -> None:
    result = subprocess.run(
        ["docker", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ApiHttpEnvironmentError(
            f"Docker command failed: {' '.join(arguments)}\n{result.stderr.strip()}"
        )


def _wait_tcp(port: int, *, label: str, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            try:
                sock.connect(("127.0.0.1", port))
            except OSError:
                time.sleep(1)
            else:
                return
    raise ApiHttpEnvironmentError(f"{label} did not open its loopback port in time")


def _verify_redis(port: int, database: int, run_id: str) -> None:
    from redis import Redis

    client = Redis(host="127.0.0.1", port=port, db=database, decode_responses=True)
    try:
        key = f"api-http:bootstrap:{run_id}"
        if not client.ping():
            raise ApiHttpEnvironmentError("Redis did not answer PING")
        client.set(key, run_id, ex=60)
        if client.get(key) != run_id:
            raise ApiHttpEnvironmentError("Redis namespace verification failed")
    finally:
        client.close()


def _run_migrations(environment: dict[str, str]) -> None:
    last_error = ""
    for _attempt in range(45):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode == 0:
            return
        last_error = (result.stderr or result.stdout).strip()
        time.sleep(2)
    raise ApiHttpEnvironmentError(f"Alembic migration failed after retries:\n{last_error}")


def _seed_users(environment: dict[str, str]) -> dict[str, SeededUser]:
    from sqlalchemy import select

    from server.infrastructure.mysql import MySQLRuntime
    from server.infrastructure.mysql.models import UserModel, WorkspaceMemberModel
    from server.rbac.service import rbac_service
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    runtime = MySQLRuntime.from_runtime(
        {
            "url": environment["NLP_AGENT_DATABASE_URL"],
            "pool_size": 2,
            "max_overflow": 0,
            "pool_recycle_s": 300,
            "connect_timeout_s": 5,
            "statement_timeout_s": 30,
        }
    )

    async def seed() -> dict[str, SeededUser]:
        await runtime.start()
        try:
            seeded: dict[str, SeededUser] = {}
            async with runtime.session_factory.begin() as session:
                service = UserService(session)
                for role in ("guest", "student", "teacher", "developer"):
                    username = f"apihttp{role}"
                    user = await session.scalar(
                        select(UserModel).where(UserModel.username == username)
                    )
                    if user is None:
                        user = await service.create_user(
                            UserCreate(
                                username=username,
                                display_name=f"API HTTP {role}",
                                password=TEST_PASSWORD,
                            )
                        )
                    await rbac_service.replace_user_roles(
                        session,
                        user_id=user.id,
                        role_codes={role},
                        assigned_by_user_id=None,
                    )
                    member = await session.scalar(
                        select(WorkspaceMemberModel)
                        .where(WorkspaceMemberModel.user_id == user.id)
                        .order_by(WorkspaceMemberModel.created_at)
                    )
                    if member is None:
                        raise ApiHttpEnvironmentError(
                            f"seeded user {username} has no workspace membership"
                        )
                    seeded[role] = SeededUser(
                        username=username,
                        password=TEST_PASSWORD,
                        user_id=str(user.id),
                        workspace_id=str(member.workspace_id),
                        role=role,
                    )
            return seeded
        finally:
            await runtime.close()

    return asyncio.run(seed())


def _safe_child_environment(
    *,
    database: str,
    redis_db: int,
    web_port: int,
    monitor_port: int,
    mysql_port: int,
    redis_port: int,
    uploads_root: Path,
) -> dict[str, str]:
    child = dict(os.environ)
    for key in (
        "NLP_AGENT_DATABASE_URL",
        "NLP_AGENT_REDIS_URL",
        "MYSQL_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_DB",
        "API_HTTP_DATABASE_URL",
        "API_HTTP_REDIS_URL",
    ):
        child.pop(key, None)
    for key in list(child):
        if key.startswith("TENCENT_SMS_"):
            child.pop(key, None)
    child.update(
        {
            "NLP_AGENT_DATABASE_URL": (
                f"mysql+aiomysql://api_http:api_http_password@127.0.0.1:{mysql_port}/{database}"
                "?charset=utf8mb4"
            ),
            "NLP_AGENT_REDIS_URL": f"redis://127.0.0.1:{redis_port}/{redis_db}",
            "NLP_AGENT_GATEWAY_TRANSPORT": "redis",
            "NLP_AGENT_WEB_SECRET": "api-http-test-secret-" + "x" * 48,
            "NLP_AGENT_STATE_FACTORY": "",
            "NLP_AGENT_WEB_HOST": "127.0.0.1",
            "NLP_AGENT_WEB_PORT": str(web_port),
            "NLP_AGENT_WEB_ALLOWED_HOSTS": "127.0.0.1,localhost,artifact.test",
            "NLP_AGENT_WEB_ALLOWED_ORIGINS": f"http://127.0.0.1:{web_port}",
            "NLP_AGENT_MONITOR_HOST": "127.0.0.1",
            "NLP_AGENT_MONITOR_PORT": str(monitor_port),
            "NLP_AGENT_MONITOR_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "NLP_AGENT_MONITOR_ALLOWED_ORIGINS": f"http://127.0.0.1:{monitor_port}",
            "NLP_AGENT_AUTH_COOKIE_SECURE": "false",
            "NLP_AGENT_SANDBOX_RUNTIME_MODE": "docker",
            "NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT": str(uploads_root / "sandbox-artifacts"),
            "NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN": "https://artifact.test",
            "NLP_AGENT_SANDBOX_APPLICATION_ORIGIN": "",
            "NLP_AGENT_UPLOADS_ROOT": str(uploads_root),
            "NLP_AGENT_API_HTTP_RUNTIME_OVERRIDES": str(uploads_root.parent / "runtime-overrides.yaml"),
            "NLP_AGENT_API_HTTP_SKILLS_ROOT": str(uploads_root.parent / ".data" / "skills"),
            "NLP_AGENT_API_HTTP_MCP_STUB": "1",
            "NLP_AGENT_API_HTTP_SMS_PROVIDER": "stub",
            # The application normalizes domestic numbers to E.164 before
            # invoking the provider, so the deterministic failure prefix is
            # expressed in that canonical form too.
            "NLP_AGENT_API_HTTP_SMS_FAILURE_PREFIX": "+86131",
            "DEEPSEEK_API_KEY": "",
            "QWEN_API_KEY": "",
            "NLP_AGENT_AUTH_USERNAME": "",
            "NLP_AGENT_AUTH_PASSWORD_HASH": "",
            "NLP_AGENT_DB_POOL_SIZE": "3",
            "NLP_AGENT_DB_MAX_OVERFLOW": "3",
        }
    )
    return child


def _wait_http(url: str, *, label: str, timeout_s: float = 90.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except httpx.HTTPError as error:
            last_error = str(error)
        time.sleep(1)
    raise ApiHttpEnvironmentError(f"{label} did not become ready: {last_error}")
