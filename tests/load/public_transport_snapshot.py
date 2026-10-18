"""Nine health GETs from an independent runner; fixed test host, no auth."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import socket
import ssl
import time

HOST, ADDRESS = "test.lsnunlp.com", "42.193.17.230"


def sample(index):
    started = time.monotonic()
    stage = "tcp_connect"
    result = {"index": index, "http_request_bytes_sent": 0}
    try:
        with socket.create_connection((ADDRESS, 443), timeout=5) as raw:
            result["tcp_ms"] = round((time.monotonic() - started) * 1000, 2)
            stage = "tls_handshake"
            with ssl.create_default_context().wrap_socket(raw, server_hostname=HOST) as secure:
                stage = "http_response"
                request = f"GET /health/live HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode()
                secure.sendall(request)
                result["http_request_bytes_sent"] = len(request)
                data = b""
                while b"\r\n" not in data and len(data) < 2048:
                    chunk = secure.recv(2048)
                    if not chunk:
                        raise ConnectionError("closed before response")
                    data += chunk
                result.update(status=int(data.split(b" ", 2)[1]), certificate_verified=True)
                result["success"] = result["status"] == 200
                if not result["success"]:
                    result["failed_stage"] = stage
    except Exception as error:
        result.update(success=False, failed_stage=stage, error_type=type(error).__name__)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main():
    started = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=3) as pool:
        samples = list(pool.map(sample, range(1, 10)))
    successful = sum(row["success"] for row in samples)
    print(json.dumps({"status": "INDEPENDENT_TEST_TRANSPORT_SNAPSHOT", "target": "https://" + HOST,
                      "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                      "attempts": 9, "successful": successful, "samples": samples,
                      "failures_by_stage": dict(Counter(row["failed_stage"] for row in samples if not row["success"])),
                      "login_attempts": 0, "model_calls": 0,
                      "scope_limit": "Different network and time window; does not locate a specific failing hop."}, indent=2))
    return 0 if successful == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
