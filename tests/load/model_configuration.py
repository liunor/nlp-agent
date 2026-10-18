"""Configure fake model I/O only after proving disposable loopback targets."""
import os
from urllib.parse import urlsplit


def configure_models() -> None:
    from configs.settings import settings
    from core.model_runtime.factory import ModelFactory

    target = urlsplit(os.environ.get("NLP_AGENT_DATABASE_URL", ""))
    endpoint = urlsplit(os.environ.get("T07_FAKE_PROVIDER_CONTROL_URL", ""))
    if (
        os.environ.get("NLP_AGENT_DEPLOYMENT_ENV") != "test"
        or target.hostname not in {"127.0.0.1", "localhost"}
        or not target.path.lstrip("/").startswith("nlp_agent_api_http_")
        or endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1"
        or endpoint.username is not None or endpoint.password is not None
    ):
        raise RuntimeError("load entrypoint requires disposable loopback test services")
    raw = settings._config
    for provider in raw["providers"].values():
        provider.update(adapter="openai_compatible", base_url=os.environ["T07_FAKE_PROVIDER_CONTROL_URL"] + "/v1", api_key_env="T07_FAKE_PROVIDER_API_KEY")
    for preset in raw["model_presets"].values():
        # The loopback fake supplies no paid native search. Leaving a Qwen-only
        # feature enabled after changing its adapter prevents the app booting.
        preset["native_search"] = {"enabled": False}
        preset["retry"] = {"max_attempts": 1}
        preset["timeouts"] = {"connect_s": 5, "first_token_s": 30, "stream_idle_s": 30, "total_s": 40}
        preset["circuit_breaker"] = {"failure_threshold": 100, "cooldown_s": 1}
    ModelFactory.config_from_raw(raw)
