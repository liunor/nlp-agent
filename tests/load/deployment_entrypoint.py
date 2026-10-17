"""Isolated load-test startup: real application/model runtime, loopback fake I/O."""

import os
from urllib.parse import urlsplit


def configure_models() -> None:
    from configs.settings import settings

    target = urlsplit(os.environ.get("NLP_AGENT_DATABASE_URL", ""))
    endpoint = urlsplit(os.environ.get("T07_FAKE_PROVIDER_CONTROL_URL", ""))
    if os.environ.get("NLP_AGENT_DEPLOYMENT_ENV") != "test" or target.hostname not in {"127.0.0.1", "localhost"} or not target.path.lstrip("/").startswith("nlp_agent_api_http_") or endpoint.hostname != "127.0.0.1":
        raise RuntimeError("load entrypoint requires disposable loopback test services")
    raw = settings._config
    for provider in raw["providers"].values():
        provider.update(adapter="openai_compatible", base_url=os.environ["T07_FAKE_PROVIDER_CONTROL_URL"] + "/v1", api_key_env="T07_FAKE_PROVIDER_API_KEY")
    for preset in raw["model_presets"].values():
        preset["retry"] = {"max_attempts": 1}
        preset["timeouts"] = {"connect_s": 5, "first_token_s": 30, "stream_idle_s": 30, "total_s": 40}
        preset["circuit_breaker"] = {"failure_threshold": 100, "cooldown_s": 1}


configure_models()
if os.environ.get("T07_LOAD_RUN_KIND") == "baseline":
    from tests.load.baseline_entrypoint import app  # noqa: F401,E402
else:
    from server.web.app import app  # noqa: F401,E402
