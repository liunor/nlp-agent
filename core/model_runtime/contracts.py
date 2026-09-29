"""Pydantic v2 configuration contracts for providers, models, presets, and routes."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReasoningEffort(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class ProviderConfig(FrozenModel):
    adapter: str
    base_url: str
    api_key_env: str
    default_headers: dict[str, str] = Field(default_factory=dict)


class ModelCapabilities(FrozenModel):
    streaming: bool = True
    tool_calls: bool = True
    thinking: bool = False
    cache_usage: bool = False
    json_mode: bool = True
    vision: bool = False
    structured_output: bool = False


class ModelDefinition(FrozenModel):
    provider: str
    model_id: str
    pricing_key: str | None = None
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class ThinkingConfig(FrozenModel):
    enabled: bool = False
    effort: ReasoningEffort = ReasoningEffort.NONE

    @model_validator(mode="after")
    def validate_effort(self) -> "ThinkingConfig":
        if not self.enabled and self.effort is not ReasoningEffort.NONE:
            raise ValueError("thinking.effort must be 'none' when thinking is disabled")
        if self.enabled and self.effort is ReasoningEffort.NONE:
            raise ValueError("thinking.effort must be set when thinking is enabled")
        return self


class GenerationConfig(FrozenModel):
    max_output_tokens: int = Field(default=16_000, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class NativeSearchConfig(FrozenModel):
    """Provider-native web search options for an explicitly opted-in preset."""

    enabled: bool = False
    forced: bool = False
    strategy: Literal["turbo", "max"] = "turbo"

    @model_validator(mode="after")
    def validate_forced_search(self) -> "NativeSearchConfig":
        if self.forced and not self.enabled:
            raise ValueError("native_search.forced requires native_search.enabled=true")
        return self


class TimeoutPolicy(FrozenModel):
    connect_s: float = Field(default=10, gt=0, le=300)
    first_token_s: float = Field(default=120, gt=0, le=1800)
    stream_idle_s: float = Field(default=60, gt=0, le=1800)
    total_s: float = Field(default=300, gt=0, le=3600)


class RetryPolicy(FrozenModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_s: float = Field(default=1, ge=0, le=60)
    max_delay_s: float = Field(default=12, ge=0, le=300)
    jitter: Literal["none", "full"] = "full"

    @model_validator(mode="after")
    def validate_delays(self) -> "RetryPolicy":
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("retry.max_delay_s must be >= base_delay_s")
        return self


class CircuitBreakerPolicy(FrozenModel):
    failure_threshold: int = Field(default=5, ge=1, le=100)
    cooldown_s: float = Field(default=60, gt=0, le=3600)


class ModelPresetConfig(FrozenModel):
    model: str
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    native_search: NativeSearchConfig = Field(default_factory=NativeSearchConfig)
    timeouts: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    circuit_breaker: CircuitBreakerPolicy = Field(default_factory=CircuitBreakerPolicy)


class ModelRouteConfig(FrozenModel):
    primary: str
    fallbacks: tuple[str, ...] = ()


class ModelProfileConfig(FrozenModel):
    label: str
    provider: str
    coordinator: str
    worker: str
    utility: str


class ModelRuntimeConfig(FrozenModel):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelDefinition]
    model_presets: dict[str, ModelPresetConfig]
    model_routes: dict[str, ModelRouteConfig]
    model_profiles: dict[str, ModelProfileConfig] = Field(default_factory=dict)
    default_model_profile: str | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "ModelRuntimeConfig":
        for model_name, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(
                    f"models.{model_name}.provider references unknown provider {model.provider!r}"
                )
        for preset_name, preset in self.model_presets.items():
            if preset.model not in self.models:
                raise ValueError(
                    f"model_presets.{preset_name}.model references unknown model {preset.model!r}"
                )
            model = self.models[preset.model]
            provider = self.providers[model.provider]
            if preset.native_search.enabled and provider.adapter != "qwen":
                raise ValueError(
                    f"preset {preset_name!r} enables native search for unsupported "
                    f"adapter {provider.adapter!r}"
                )
            if preset.thinking.enabled and not model.capabilities.thinking:
                raise ValueError(f"model {preset.model!r} does not support thinking")
            if preset.generation.max_output_tokens > model.max_output_tokens:
                raise ValueError(
                    f"preset {preset_name!r} exceeds model max_output_tokens"
                )
        for route_name, route in self.model_routes.items():
            names = (route.primary, *route.fallbacks)
            missing = [name for name in names if name not in self.model_presets]
            if missing:
                raise ValueError(f"model_routes.{route_name} references unknown presets: {missing}")
            primary = self.models[self.model_presets[route.primary].model]
            for fallback_name in route.fallbacks:
                fallback = self.models[self.model_presets[fallback_name].model]
                required_capabilities = (
                    ("tool_calls", "tool-call"),
                    ("streaming", "streaming"),
                    ("vision", "vision"),
                    ("structured_output", "structured-output"),
                )
                for field, label in required_capabilities:
                    if getattr(primary.capabilities, field) and not getattr(
                        fallback.capabilities, field
                    ):
                        raise ValueError(
                            f"fallback {fallback_name!r} lacks {label} capability "
                            f"required by {route.primary!r}"
                        )
        vision_route = self.model_routes.get("vision-worker")
        if vision_route is not None:
            if vision_route.primary != "vision-qwen-plus" or vision_route.fallbacks:
                raise ValueError(
                    "model_routes.vision-worker must use only vision-qwen-plus"
                )
            vision_preset = self.model_presets[vision_route.primary]
            vision_model = self.models[vision_preset.model]
            if vision_model.model_id != "qwen3-vl-plus":
                raise ValueError(
                    "vision-qwen-plus must resolve to qwen3-vl-plus"
                )
            if (
                not vision_model.capabilities.vision
                or not vision_model.capabilities.structured_output
            ):
                raise ValueError(
                    "vision-worker requires vision and structured_output capabilities"
                )
        for profile_name, profile in self.model_profiles.items():
            if profile.provider not in self.providers:
                raise ValueError(
                    f"model_profiles.{profile_name}.provider references unknown provider "
                    f"{profile.provider!r}"
                )
            for role in ("coordinator", "worker", "utility"):
                preset_name = getattr(profile, role)
                if preset_name not in self.model_presets:
                    raise ValueError(
                        f"model_profiles.{profile_name}.{role} references unknown preset "
                        f"{preset_name!r}"
                    )
                model = self.models[self.model_presets[preset_name].model]
                if model.provider != profile.provider:
                    raise ValueError(
                        f"model_profiles.{profile_name}.{role} uses provider "
                        f"{model.provider!r}, expected {profile.provider!r}"
                    )
        if (
            self.default_model_profile is not None
            and self.default_model_profile not in self.model_profiles
        ):
            raise ValueError(
                f"default model profile {self.default_model_profile!r} is not configured"
            )
        return self

    def route_presets(self, route_name: str) -> tuple[tuple[str, ModelPresetConfig], ...]:
        try:
            route = self.model_routes[route_name]
        except KeyError as error:
            raise KeyError(f"Unknown model route {route_name!r}") from error
        return tuple(
            (name, self.model_presets[name]) for name in (route.primary, *route.fallbacks)
        )

    def preset(self, name: str) -> ModelPresetConfig:
        try:
            return self.model_presets[name]
        except KeyError as error:
            raise KeyError(f"Unknown model preset {name!r}") from error

    def profile(self, name: str) -> ModelProfileConfig:
        try:
            return self.model_profiles[name]
        except KeyError as error:
            raise KeyError(f"Unknown model profile {name!r}") from error
