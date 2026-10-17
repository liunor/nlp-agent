"""Loopback-only, deterministic OpenAI-compatible fake for T07 load tests."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any


class FakeProviderState:
    def __init__(self, *, delay_s: float, timeout_delay_s: float) -> None:
        self.delay_s = delay_s
        self.timeout_delay_s = timeout_delay_s
        self.mode = "success"
        self.calls_total = 0
        self._lock = Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"mode": self.mode, "calls_total": self.calls_total}

    def set_mode(self, mode: str) -> None:
        if mode not in {"success", "http_401", "http_429", "http_503", "timeout"}:
            raise ValueError("unsupported fake Provider mode")
        with self._lock:
            self.mode = mode

    def begin_request(self) -> str:
        with self._lock:
            self.calls_total += 1
            return self.mode


class FakeProviderHandler(BaseHTTPRequestHandler):
    server: "FakeProviderServer"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, response: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        response_id = response["id"]
        model = response["model"]
        chunks = [
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": response["choices"][0]["message"]["content"]}, "finish_reason": None}],
            },
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": model,
                "choices": [],
                "usage": response["usage"],
            },
        ]
        for chunk in chunks:
            payload = json.dumps(chunk, separators=(",", ":")).encode("utf-8")
            self.wfile.write(b"data: " + payload + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/__t07__/health":
            self._json(404, {"error": "not_found"})
            return
        if not self._authorized_control_request():
            self._json(403, {"error": "forbidden"})
            return
        self._json(200, self.server.state.snapshot())

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/__t07__/mode":
            if not self._authorized_control_request():
                self._json(403, {"error": "forbidden"})
                return
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                self.server.state.set_mode(str(payload.get("mode", "")))
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_mode"})
                return
            self._json(200, self.server.state.snapshot())
            return

        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not_found", "type": "invalid_request_error"}})
            return

        mode = self.server.state.begin_request()
        if mode == "timeout":
            time.sleep(self.server.state.timeout_delay_s)
        elif mode != "success":
            status, code = {
                "http_401": (401, "invalid_api_key"),
                "http_429": (429, "rate_limit_exceeded"),
                "http_503": (503, "service_unavailable"),
            }[mode]
            self._json(
                status,
                {"error": {"message": code, "type": code, "code": code}},
            )
            return
        else:
            time.sleep(self.server.state.delay_s)

        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": {"message": "invalid_json"}})
            return

        content = "T07 deterministic fake response"
        input_tokens = max(1, len(json.dumps(request.get("messages", []))) // 4)
        output_tokens = max(1, len(content) // 4)
        response = {
            "id": f"t07-fake-{secrets.token_hex(8)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(request.get("model") or "t07-fake-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }
        if request.get("stream"):
            self._stream(response)
        else:
            self._json(200, response)

    def _authorized_control_request(self) -> bool:
        peer = self.client_address[0]
        if peer not in {"127.0.0.1", "::1"}:
            return False
        expected = self.server.control_token
        provided = self.headers.get("X-T07-Control-Token", "")
        return bool(expected) and secrets.compare_digest(provided, expected)


class FakeProviderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, state: FakeProviderState, control_token: str) -> None:
        super().__init__(address, FakeProviderHandler)
        self.state = state
        self.control_token = control_token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--delay-seconds", type=float, default=20.0)
    parser.add_argument("--timeout-delay-seconds", type=float, default=70.0)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("the fake Provider must bind to loopback only")
    token = os.environ.get("T07_FAKE_PROVIDER_CONTROL_TOKEN", "")
    if not token:
        parser.error("T07_FAKE_PROVIDER_CONTROL_TOKEN must be set")
    server = FakeProviderServer(
        (args.host, args.port),
        state=FakeProviderState(
            delay_s=max(0.0, args.delay_seconds),
            timeout_delay_s=max(0.0, args.timeout_delay_seconds),
        ),
        control_token=token,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
