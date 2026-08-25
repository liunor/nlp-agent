"""Dedicated Process entry point for Phase 2 Sandbox Manager.

Run this separately from the Web service.  Only this process is granted Docker
Engine access; its runtime image must be pinned to a digest.
"""

from __future__ import annotations

import asyncio

from configs.settings import settings
from server.infrastructure.mysql.config import DatabaseConfig
from server.infrastructure.mysql.engine import create_engine, create_session_factory

from .docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
from .manager import WarmPoolManager


async def run_forever() -> None:
    image = settings.NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST.strip()
    target = settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET
    if not image or target <= 0:
        raise RuntimeError(
            "Set NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST and a positive "
            "NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET before starting Sandbox Manager."
        )
    engine = create_engine(DatabaseConfig.from_runtime(settings.database_runtime))
    manager = WarmPoolManager(
        session_factory=create_session_factory(engine),
        docker=DockerRuntimeAdapter(DockerRuntimeConfig(image=image)),
        resource_profile_id="python-base",
        ready_target=target,
    )
    try:
        while True:
            await manager.reconcile()
            await asyncio.sleep(max(5, settings.NLP_AGENT_SANDBOX_RECONCILE_INTERVAL_S))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
