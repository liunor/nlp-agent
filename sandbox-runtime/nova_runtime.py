"""Container-local stdin/JSON bridge to the long-lived ipykernel.

Only Sandbox Manager invokes this program through ``docker exec``.  It opens
no network port and does not receive platform credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Literal

KERNEL_CONNECTION_FILE = "/run/nova/kernel.json"


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

    def to_payload(self) -> dict[str, object]:
        return {
            "stdout": "".join(self._stdout),
            "stderr": "".join(self._stderr),
            "truncated": self._truncated,
        }


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
                output.append("stderr", "\n".join(content.get("traceback", [])) + "\n")
            elif message_type == "execute_result":
                output.append("stdout", content.get("data", {}).get("text/plain", "") + "\n")
            elif message_type == "status" and content.get("execution_state") == "idle":
                return output.to_payload()
    finally:
        client.stop_channels()


def interrupt() -> None:
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=KERNEL_CONNECTION_FILE)
    client.load_connection_file()
    client.interrupt_kernel()


def health() -> None:
    """Round-trip kernel_info; a connection file alone is not a readiness signal."""
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
    return output.to_payload()


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
    except (TimeoutError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
