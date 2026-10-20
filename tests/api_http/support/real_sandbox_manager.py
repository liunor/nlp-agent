"""Opt-in isolated CI process: production Manager/RPC with a built local image."""

import os
import re
from urllib.parse import urlsplit

from configs.settings import settings
from server.sandbox import manager_runner


def main() -> None:
    database = urlsplit(settings.NLP_AGENT_DATABASE_URL)
    if (os.environ.get("NLP_AGENT_API_HTTP_REAL_SANDBOX") != "1"
            or os.environ.get("RUN_SANDBOX_DOCKER_MANAGER_INTEGRATION") != "1"
            or database.hostname != "127.0.0.1"
            or not re.fullmatch(r"/nlp_agent_api_http_[0-9a-f]{12}", database.path)
            or not re.fullmatch(r"api-http-[0-9a-f]{12}", settings.NLP_AGENT_SANDBOX_NAMESPACE)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", settings.NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST)):
        raise RuntimeError("real Sandbox test Manager requires isolated HTTP-test resources")
    factory = manager_runner.create_runtime_adapter

    def local_image_factory(**kwargs):
        return factory(**kwargs, allow_local_image_id=True)

    # Only this CI process accepts its already-built image ID. Production's
    # Manager still requires a registry digest and has no env switch for this.
    manager_runner.create_runtime_adapter = local_image_factory
    manager_runner.main()


if __name__ == "__main__":
    main()
