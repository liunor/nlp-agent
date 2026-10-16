"""Run the single-process FastAPI + BackendGateway service."""

from __future__ import annotations

import uvicorn

from configs.settings import settings


def run() -> None:
    config = settings.web_runtime
    uvicorn.run(
        "server.web.app:app",
        host=str(config.get("host", "127.0.0.1")),
        port=int(config.get("port", 8765)),
        workers=1,
        forwarded_allow_ips=config.get("forwarded_allow_ips"),
        ws_max_size=int(config.get("max_ws_message_bytes", 1_048_576)),
        ws_ping_interval=float(config.get("ws_ping_interval_s", 20)),
        ws_ping_timeout=float(config.get("ws_ping_timeout_s", 20)),
    )


if __name__ == "__main__":
    run()
