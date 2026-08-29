"""Container-local stdin/JSON bridge to the long-lived ipykernel.

Only Sandbox Manager invokes this program through ``docker exec``.  It opens
no network port and does not receive platform credentials.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
import signal
import sys
import time
from dataclasses import dataclass
from typing import Literal

KERNEL_CONNECTION_FILE = "/run/nova/kernel.json"
ARTIFACT_DIRECTORY = Path("/workspace/artifacts")
MAX_ARTIFACT_FILES = 16
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
EXPECTED_TORCH_VERSION = "2.7.1"


def verify_runtime_dependencies() -> None:
    """Fail readiness when the image does not contain the promised CPU runtime."""
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("required runtime dependency torch is unavailable") from error
    installed_version = str(torch.__version__).split("+", 1)[0]
    if installed_version != EXPECTED_TORCH_VERSION:
        raise RuntimeError(
            f"torch version mismatch: expected {EXPECTED_TORCH_VERSION}, got {installed_version}"
        )
    if torch.cuda.is_available():
        raise RuntimeError("CPU-only PyTorch runtime unexpectedly exposes CUDA")


class OutputCollector:
    def __init__(self, *, limit_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._used_bytes = 0
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._truncated = False

    def append(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        remaining = self._limit_bytes - self._used_bytes
        encoded = text.encode("utf-8")
        if remaining <= 0:
            self._truncated = True
            return
        clipped = encoded[:remaining]
        if len(clipped) < len(encoded):
            self._truncated = True
        content = clipped.decode("utf-8", "ignore")
        self._used_bytes += len(content.encode("utf-8"))
        (self._stdout if stream == "stdout" else self._stderr).append(content)

    def to_payload(self, *, status: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "stdout": "".join(self._stdout),
            "stderr": "".join(self._stderr),
            "truncated": self._truncated,
        }
        if status is not None:
            payload["status"] = status
        return payload


@dataclass(frozen=True)
class ExecuteRequest:
    source: str
    timeout_seconds: int
    output_limit_bytes: int


def parse_execute_request(payload: dict[str, object]) -> ExecuteRequest:
    source = payload.get("source")
    timeout_seconds = payload.get("timeout_seconds", 15)
    output_limit_bytes = payload.get("output_limit_bytes", 1_000_000)
    if not isinstance(source, str) or not source:
        raise ValueError("source must be a non-empty string")
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be between 1 and 60")
    if not isinstance(output_limit_bytes, int) or not 1 <= output_limit_bytes <= 1_000_000:
        raise ValueError("output_limit_bytes must be between 1 and 1000000")
    return ExecuteRequest(source, timeout_seconds, output_limit_bytes)


def execute(request: ExecuteRequest) -> dict[str, object]:
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=KERNEL_CONNECTION_FILE)
    client.load_connection_file()
    client.start_channels()
    output = OutputCollector(limit_bytes=request.output_limit_bytes)
    deadline = time.monotonic() + request.timeout_seconds
    failed = False
    try:
        message_id = client.execute(request.source, allow_stdin=False, store_history=True)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                client.interrupt_kernel()
                raise TimeoutError("kernel execution timed out")
            message = client.get_iopub_msg(timeout=remaining)
            if message.get("parent_header", {}).get("msg_id") != message_id:
                continue
            message_type = message["header"]["msg_type"]
            content = message["content"]
            if message_type == "stream":
                output.append(content.get("name", "stdout"), content.get("text", ""))
            elif message_type == "error":
                failed = True
                output.append("stderr", "\n".join(content.get("traceback", [])) + "\n")
            elif message_type == "execute_result":
                output.append("stdout", content.get("data", {}).get("text/plain", "") + "\n")
            elif message_type == "status" and content.get("execution_state") == "idle":
                result = output.to_payload(status="failed" if failed else "completed")
                result["artifacts"] = collect_workspace_artifacts()
                return result
    finally:
        client.stop_channels()


def interrupt() -> None:
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=KERNEL_CONNECTION_FILE)
    client.load_connection_file()
    client.interrupt_kernel()


def health() -> None:
    """Round-trip kernel_info; a connection file alone is not a readiness signal."""
    verify_runtime_dependencies()
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=KERNEL_CONNECTION_FILE)
    client.load_connection_file()
    client.start_channels()
    try:
        message_id = client.kernel_info()
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("kernel health probe timed out")
            message = client.get_shell_msg(timeout=remaining)
            if message.get("parent_header", {}).get("msg_id") == message_id:
                return
    finally:
        client.stop_channels()


def scratch(request: ExecuteRequest) -> dict[str, object]:
    """Execute in a fresh process: intentionally no access to Interactive Kernel state."""
    output = OutputCollector(limit_bytes=request.output_limit_bytes)
    namespace = {"__builtins__": __builtins__}
    def deadline_exceeded(_signum: int, _frame: object) -> None:
        raise TimeoutError("scratch execution timed out")

    alarm_enabled = hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")
    previous_handler = None
    previous_timer: tuple[float, float] | None = None
    if alarm_enabled:
        previous_handler = signal.signal(signal.SIGALRM, deadline_exceeded)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, request.timeout_seconds)
    try:
        import contextlib
        import io

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compile(request.source, "<model-scratch>", "exec"), namespace, namespace)
        output.append("stdout", stdout.getvalue())
        output.append("stderr", stderr.getvalue())
    except Exception as error:
        output.append("stderr", f"{type(error).__name__}: {error}\n")
        result = output.to_payload(status="failed")
        result["artifacts"] = collect_workspace_artifacts()
        return result
    finally:
        if alarm_enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if previous_handler is not None:
                signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer is not None and previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    result = output.to_payload(status="completed")
    result["artifacts"] = collect_workspace_artifacts()
    return result


def collect_workspace_artifacts(root: Path = ARTIFACT_DIRECTORY) -> list[dict[str, str]]:
    """Return a bounded, explicit artifact directory; never arbitrary files."""
    if not root.is_dir():
        return []
    collected: list[dict[str, str]] = []
    used = 0
    for path in sorted(root.rglob("*")):
        if len(collected) >= MAX_ARTIFACT_FILES or not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(root)
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_ARTIFACT_BYTES or used + len(data) > MAX_ARTIFACT_BYTES:
            continue
        used += len(data)
        collected.append({
            "name": relative.as_posix(),
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "content_b64": base64.b64encode(data).decode("ascii"),
        })
    return collected


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    execute_parser = subcommands.add_parser("execute")
    execute_parser.add_argument("--timeout-seconds", type=int, required=True)
    execute_parser.add_argument("--output-limit-bytes", type=int, required=True)
    subcommands.add_parser("interrupt")
    subcommands.add_parser("health")
    scratch_parser = subcommands.add_parser("scratch")
    scratch_parser.add_argument("--timeout-seconds", type=int, required=True)
    scratch_parser.add_argument("--output-limit-bytes", type=int, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "interrupt":
            interrupt()
            return 0
        if arguments.command == "health":
            health()
            return 0
        wire_payload = json.load(sys.stdin)
        request = parse_execute_request({
            **wire_payload,
            "timeout_seconds": arguments.timeout_seconds,
            "output_limit_bytes": arguments.output_limit_bytes,
        })
        result = scratch(request) if arguments.command == "scratch" else execute(request)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TimeoutError, ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
