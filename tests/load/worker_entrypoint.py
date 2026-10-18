"""Run the real Worker/model runtime against the isolated fake HTTP Provider."""

import asyncio
from tests.load.model_configuration import configure_models
from server.worker.runtime import run_worker


if __name__ == "__main__":
    configure_models()
    asyncio.run(run_worker())
