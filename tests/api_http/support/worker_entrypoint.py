"""Process entrypoint for the isolated Redis-backed Phase 5 Worker."""

from __future__ import annotations

import asyncio

from server.worker.runtime import run_worker

from .worker_model import install_deterministic_worker_model


def main() -> None:
    install_deterministic_worker_model()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
