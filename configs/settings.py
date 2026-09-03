"""Application settings and compatibility accessors for typed model routes."""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.runtime_config import load_runtime_config
from server.quota.rollout import QuotaRollout


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    BASE_DIR: Path = BASE_DIR
    DEEPSEEK_API_KEY: str = ""
    QWEN_API_KEY: str = ""
    NLP_AGENT_WORKER_MODEL: str = ""
    NLP_AGENT_WEB_SECRET: str = ""
    NLP_AGENT_AUTH_USERNAME: str = ""
    NLP_AGENT_AUTH_PASSWORD_HASH: str = ""
    NLP_AGENT_AUTH_ROLES: str = "student"
    NLP_AGENT_AUTH_SESSION_TTL_S: int = 1800
    NLP_AGENT_AUTH_IDLE_TIMEOUT_S: int = 900
    NLP_AGENT_AUTH_MAX_LOGIN_ATTEMPTS: int = 5
    NLP_AGENT_AUTH_RATE_WINDOW_S: int = 300
    NLP_AGENT_SMS_DEVELOPMENT_MODE: bool = False
    NLP_AGENT_AUTH_COOKIE_SECURE: bool | None = None
    NLP_AGENT_AUDIT_SUCCESSFUL_READS: bool = False
    NLP_AGENT_WEB_HOST: str = ""
    NLP_AGENT_WEB_PORT: int = 0
    NLP_AGENT_WEB_ALLOWED_HOSTS: str = ""
    NLP_AGENT_WEB_ALLOWED_ORIGINS: str = ""
    NLP_AGENT_MONITOR_HOST: str = ""
    NLP_AGENT_MONITOR_PORT: int = 0
    NLP_AGENT_MONITOR_ALLOWED_HOSTS: str = ""
    NLP_AGENT_MONITOR_ALLOWED_ORIGINS: str = ""
    NLP_AGENT_GATEWAY_TRANSPORT: str = ""
    NLP_AGENT_REDIS_URL: str = ""
    NLP_AGENT_STATE_FACTORY: str = ""
    NLP_AGENT_DATABASE_URL: str = ""
    NLP_AGENT_QUOTA_ENFORCEMENT: bool = False
    NLP_AGENT_QUOTA_ENFORCEMENT_PERCENT: int | None = None
    NLP_AGENT_QUOTA_ENFORCEMENT_USERS: str = ""
    NLP_AGENT_QUOTA_ENFORCEMENT_WORKSPACES: str = ""
    NLP_AGENT_DB_POOL_SIZE: int = 10
    NLP_AGENT_DB_MAX_OVERFLOW: int = 20
    NLP_AGENT_DB_POOL_RECYCLE_S: int = 1800
    NLP_AGENT_DB_CONNECT_TIMEOUT_S: int = 5
    NLP_AGENT_DB_STATEMENT_TIMEOUT_S: int = 30
    # Timezone used to bucket teacher-analytics hour/weekday and peak-hour
    # distributions.  Fixed offset only (no DST); see gateway.analytics_time.
    NLP_AGENT_ANALYTICS_TIMEZONE: str = "Asia/Shanghai"
    # In-process execution is deliberately opt-in.  It exists solely for
    # Phase 1 local Workbench development and is unsafe for untrusted code.
    NLP_AGENT_SANDBOX_RUNTIME_MODE: str = "disabled"
    NLP_AGENT_SANDBOX_RUNTIME_BACKEND: str = "runsc"
    NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST: str = ""
    NLP_AGENT_SANDBOX_KUBERNETES_CLIENT_FACTORY: str = ""
    NLP_AGENT_SANDBOX_FIRECRACKER_KERNEL_IMAGE: str = ""
    NLP_AGENT_SANDBOX_FIRECRACKER_ROOTFS_IMAGE: str = ""
    NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET: int = 0
    NLP_AGENT_SANDBOX_ADAPTIVE_POOL_ENABLED: bool = False
    NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN: int = 1
    NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX: int = 5
    NLP_AGENT_SANDBOX_BURST_BUFFER: int = 1
    NLP_AGENT_SANDBOX_ARRIVAL_RATE_PER_MIN: float = 0.0
    NLP_AGENT_SANDBOX_REFILL_P95_S: float = 4.0
    NLP_AGENT_SANDBOX_FAULT_INJECTION: str = ""
    NLP_AGENT_SANDBOX_PRELOAD_MATRIX_PATH: str = ""
    NLP_AGENT_SANDBOX_RECONCILE_INTERVAL_S: int = 30
    NLP_AGENT_SANDBOX_EVENT_RETENTION_S: int = 86_400
    NLP_AGENT_SANDBOX_EVENT_MAXLEN: int = 10_000
    NLP_AGENT_SANDBOX_COMMAND_RETENTION_S: int = 86_400
    # Scratch permits up to 60 seconds; keep RPC response budget above that
    # limit so a valid long-running execution cannot outlive its Web request.
    NLP_AGENT_SANDBOX_MANAGER_RPC_TIMEOUT_S: float = 75.0
    NLP_AGENT_SANDBOX_METRICS_RETENTION_S: int = 7 * 24 * 3600
    NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN: str = ""
    # Public Nova origin allowed to embed artifact documents.  It is kept
    # separate from the artifact host so the delivery service can emit a
    # precise frame-ancestors policy without trusting request Host headers.
    NLP_AGENT_SANDBOX_APPLICATION_ORIGIN: str = ""
    NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT: str = ""
    NLP_AGENT_SANDBOX_ARTIFACT_TTL_S: int = 3600
    NLP_AGENT_SANDBOX_PROJECT_STORAGE_ENABLED: bool = False
    NLP_AGENT_SANDBOX_PROJECT_STORAGE_ROOT: str = ""
    NLP_AGENT_SANDBOX_SNAPSHOTS_ENABLED: bool = False

    _config: dict = {}

    def __init__(self, **values):
        super().__init__(**values)
        self._config = load_runtime_config()

    def _get_llm_config(self, name: str) -> dict:
        presets = self._config.get("model_presets", {})
        models = self._config.get("models", {})
        if name in presets:
            preset_name, preset, model_name = (
                name,
                presets[name],
                presets[name]["model"],
            )
        elif name in models:
            preset_name, preset, model_name = (
                name,
                {"generation": {}, "thinking": {}},
                name,
            )
        else:
            raise KeyError(
                f"Unknown model preset/model {name!r}; presets={list(presets)}"
            )
        model = models[model_name]
        provider_name = model["provider"]
        provider = self._config.get("providers", {})[provider_name]
        env_name = provider.get("api_key_env", "DEEPSEEK_API_KEY")
        generation = preset.get("generation", {})
        thinking = preset.get("thinking", {})
        return {
            "preset": preset_name,
            "model_name": model_name,
            "model_id": model["model_id"],
            "provider": provider_name,
            "base_url": provider["base_url"],
            "api_key_configured": bool(getattr(self, env_name, "")),
            "context_window_tokens": int(model["context_window_tokens"]),
            "output_reserve_tokens": int(generation.get("max_output_tokens", 16_000)),
            "thinking_enabled": bool(thinking.get("enabled", False)),
            "reasoning_effort": thinking.get("effort", "none"),
        }

    def get_context_limits(self, preset_name: str | None = None) -> tuple[int, int]:
        name = preset_name or self._config.get("defaults", {}).get(
            "coordinator", "coordinator-pro"
        )
        preset_names = [name]
        for route in self._config.get("model_routes", {}).values():
            if route.get("primary") == name:
                preset_names.extend(route.get("fallbacks", []))
                break
        details = [self._get_llm_config(item) for item in preset_names]
        return (
            min(item["context_window_tokens"] for item in details),
            max(item["output_reserve_tokens"] for item in details),
        )

    @property
    def memory_runtime(self) -> dict:
        return dict(self._config.get("memory", {}))

    @property
    def prompt_runtime(self) -> dict:
        return dict(self._config.get("prompts", {}))

    def get_agent_runtime(self, role: str) -> dict:
        return dict(self._config.get("agent_runtime", {}).get(role, {}))

    @property
    def gateway_runtime(self) -> dict:
        config = dict(self._config.get("gateway", {}))
        if self.NLP_AGENT_GATEWAY_TRANSPORT.strip():
            config["transport"] = self.NLP_AGENT_GATEWAY_TRANSPORT.strip()
        if self.NLP_AGENT_REDIS_URL.strip():
            config["redis_url"] = self.NLP_AGENT_REDIS_URL.strip()
        if self.NLP_AGENT_STATE_FACTORY.strip():
            config["state_factory"] = self.NLP_AGENT_STATE_FACTORY.strip()
        if self.NLP_AGENT_QUOTA_ENFORCEMENT_PERCENT is not None:
            config["quota_enforcement_percentage"] = self.NLP_AGENT_QUOTA_ENFORCEMENT_PERCENT
        if self.NLP_AGENT_QUOTA_ENFORCEMENT_USERS.strip():
            config["quota_enforcement_users"] = self.NLP_AGENT_QUOTA_ENFORCEMENT_USERS
        if self.NLP_AGENT_QUOTA_ENFORCEMENT_WORKSPACES.strip():
            config["quota_enforcement_workspaces"] = self.NLP_AGENT_QUOTA_ENFORCEMENT_WORKSPACES
        return config

    @property
    def quota_rollout(self) -> QuotaRollout:
        return QuotaRollout.from_config(
            self.gateway_runtime,
            global_enabled=bool(
                self.NLP_AGENT_QUOTA_ENFORCEMENT
                or self.gateway_runtime.get("quota_enforcement", False)
            ),
        )

    @property
    def quota_enforcement_enabled(self) -> bool:
        """Whether any rollout target requires quota services at startup."""
        return self.quota_rollout.configured

    def quota_enforcement_for(self, user_id: str, workspace_id: str | None) -> bool:
        return self.quota_rollout.enabled_for(user_id, workspace_id)

    @property
    def database_runtime(self) -> dict:
        config = dict(self._config.get("database", {}))
        config.update(
            {
                "url": self.NLP_AGENT_DATABASE_URL.strip(),
                "pool_size": self.NLP_AGENT_DB_POOL_SIZE,
                "max_overflow": self.NLP_AGENT_DB_MAX_OVERFLOW,
                "pool_recycle_s": self.NLP_AGENT_DB_POOL_RECYCLE_S,
                "connect_timeout_s": self.NLP_AGENT_DB_CONNECT_TIMEOUT_S,
                "statement_timeout_s": self.NLP_AGENT_DB_STATEMENT_TIMEOUT_S,
            }
        )
        return config

    @property
    def web_runtime(self) -> dict:
        config = dict(self._config.get("web", {}))
        if self.NLP_AGENT_WEB_SECRET:
            config["auth_secret"] = self.NLP_AGENT_WEB_SECRET
        if self.NLP_AGENT_AUTH_USERNAME:
            config["auth_username"] = self.NLP_AGENT_AUTH_USERNAME
        if self.NLP_AGENT_AUTH_PASSWORD_HASH:
            config["auth_password_hash"] = self.NLP_AGENT_AUTH_PASSWORD_HASH
        config["auth_roles"] = self.NLP_AGENT_AUTH_ROLES
        config["audit_successful_reads"] = self.NLP_AGENT_AUDIT_SUCCESSFUL_READS
        self._apply_network_overrides(
            config,
            host=self.NLP_AGENT_WEB_HOST,
            port=self.NLP_AGENT_WEB_PORT,
            allowed_hosts=self.NLP_AGENT_WEB_ALLOWED_HOSTS,
            allowed_origins=self.NLP_AGENT_WEB_ALLOWED_ORIGINS,
        )
        return config

    @property
    def monitor_runtime(self) -> dict:
        config = dict(self._config.get("monitor", {}))
        if self.NLP_AGENT_WEB_SECRET:
            config["auth_secret"] = self.NLP_AGENT_WEB_SECRET
        self._apply_network_overrides(
            config,
            host=self.NLP_AGENT_MONITOR_HOST,
            port=self.NLP_AGENT_MONITOR_PORT,
            allowed_hosts=self.NLP_AGENT_MONITOR_ALLOWED_HOSTS,
            allowed_origins=self.NLP_AGENT_MONITOR_ALLOWED_ORIGINS,
        )
        return config

    @staticmethod
    def _apply_network_overrides(
        config: dict,
        *,
        host: str,
        port: int,
        allowed_hosts: str,
        allowed_origins: str,
    ) -> None:
        if host.strip():
            config["host"] = host.strip()
        if port > 0:
            config["port"] = port
        if values := [
            item.strip() for item in allowed_hosts.split(",") if item.strip()
        ]:
            config["allowed_hosts"] = values
        if values := [
            item.strip() for item in allowed_origins.split(",") if item.strip()
        ]:
            config["allowed_origins"] = values

    def _resolve_worker_model(
        self,
        agent_name: str | None = None,
        requested_model: str | None = None,
    ) -> str:
        if self.NLP_AGENT_WORKER_MODEL:
            return self.NLP_AGENT_WORKER_MODEL
        if requested_model not in (None, "", "inherit"):
            return str(requested_model)
        agent_config = self._config.get("agents", {}).get(agent_name or "", {})
        if agent_config.get("model") not in (None, "", "inherit"):
            return str(agent_config["model"])
        default = self._config.get("defaults", {}).get("worker", "inherit")
        if default != "inherit":
            return str(default)
        return str(
            self._config.get("defaults", {}).get("coordinator", "coordinator-pro")
        )

    def _resolve_model_name(
        self,
        agent_name: str | None = None,
        requested_model: str | None = None,
    ) -> str:
        """Compatibility alias retained for Worker metadata and recovery."""
        return self._resolve_worker_model(agent_name, requested_model)

    @property
    def planner_llm(self) -> dict:
        name = (
            self._config.get("model_routes", {})
            .get("coordinator", {})
            .get("primary", "coordinator-pro")
        )
        return self._get_llm_config(name)

    @property
    def tool_llm(self) -> dict:
        return self._get_llm_config(self._resolve_worker_model())

    def resolve_worker_llm(
        self,
        agent_name: str | None = None,
        tool_specified_model: str | None = None,
    ) -> dict:
        return self._get_llm_config(
            self._resolve_worker_model(agent_name, tool_specified_model)
        )

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        extra="ignore",
    )


settings = Settings()

_AUTH_DOTENV = dotenv_values(BASE_DIR / ".env")


def auth_env_value(name: str, default: str | None = None) -> str | None:
    """Read auth settings with the same precedence as model API keys.

    ``os.environ`` wins, then the pydantic-settings populated field (which
    already reflects ``.env``), then a direct ``.env`` lookup as a fallback.
    """
    if name in os.environ:
        return os.environ[name]
    value = getattr(settings, name, None)
    if value is not None:
        return str(value)
    if name in _AUTH_DOTENV:
        return str(_AUTH_DOTENV[name])
    return default


def auth_env_int(name: str, default: int) -> int:
    raw = auth_env_value(name)
    return int(raw) if raw not in (None, "") else default


def auth_session_ttl_s(default: int = 86_400) -> int:
    """Get session TTL in seconds with fallback from auth_session_ttl_s to cookie_ttl_s."""
    # Try the new environment variable first
    auth_ttl = auth_env_int("NLP_AGENT_AUTH_SESSION_TTL_S", None)
    if auth_ttl is not None:
        return auth_ttl

    # Fall back to legacy cookie_ttl_s environment variable
    cookie_ttl = auth_env_int("NLP_AGENT_COOKIE_TTL_S", None)
    if cookie_ttl is not None:
        return cookie_ttl

    # Use the default if neither is set
    return default


_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def auth_env_bool(name: str, default: bool) -> bool:
    raw = auth_env_value(name)
    if raw is None or raw == "":
        return default
    value = str(raw).strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    # An unrecognized value (e.g. a typo like ``ture``) is a misconfiguration,
    # not "false".  Fail loudly instead of silently disabling cookie security.
    raise ValueError(
        f"{name} must be a boolean (true/false, 1/0, yes/no, on/off), got {raw!r}"
    )
