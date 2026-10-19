"""Redirect model I/O only after proving disposable loopback test targets."""
import os
from urllib.parse import urlsplit


def configure_models() -> None:
    database = urlsplit(os.environ.get("NLP_AGENT_DATABASE_URL", ""))
    endpoint = urlsplit(os.environ.get("T07_FAKE_PROVIDER_CONTROL_URL", ""))
    if (os.environ.get("NLP_AGENT_DEPLOYMENT_ENV") != "test"
            or database.scheme != "mysql+aiomysql"
            or database.hostname not in {"127.0.0.1", "localhost"}
            or not database.path.lstrip("/").startswith("nlp_agent_api_http_")
            or endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1"
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
        raise RuntimeError("load entrypoint requires disposable loopback test services")
    from configs.settings import settings
    from core.model_runtime.factory import ModelFactory

    raw = settings._config
    for provider in raw["providers"].values():
        provider.update(adapter="openai_compatible", base_url=os.environ["T07_FAKE_PROVIDER_CONTROL_URL"].rstrip("/") + "/v1",
                        api_key_env="T07_FAKE_PROVIDER_API_KEY")
    for preset in raw["model_presets"].values():
        # The fake has no vendor-native paid search. Validate the changed adapter
        # before launching Web/Worker instead of discovering it as a startup timeout.
        preset["native_search"] = {"enabled": False}
        preset["retry"] = {"max_attempts": 1}
        preset["timeouts"] = {"connect_s": 5, "first_token_s": 30, "stream_idle_s": 30, "total_s": 40}
        preset["circuit_breaker"] = {"failure_threshold": 100, "cooldown_s": 1}
    # The explicit Qwen profile pins a single candidate, so its Provider timeout
    # can settle before the coordinator route's independent 30-second watchdog.
    # This avoids a race that would test a workflow deadline instead of the
    # intended upstream_timeout contract. Normal 20-second fake work still fits.
    timeout_preset = raw["model_profiles"]["qwen"]["coordinator"]
    raw["model_presets"][timeout_preset]["timeouts"] = {
        "connect_s": 5, "first_token_s": 25, "stream_idle_s": 25, "total_s": 25}
    ModelFactory.config_from_raw(raw)
