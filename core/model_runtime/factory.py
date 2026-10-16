"""Build resilient model routes from explicit typed configuration."""

from __future__ import annotations

import os
from typing import Any

from dotenv import dotenv_values

from configs.settings import BASE_DIR, settings
from core.model_runtime.adapters.deepseek import DeepSeekAdapter
from core.model_runtime.adapters.glm import GLMAdapter
from core.model_runtime.adapters.kimi import KimiAdapter
from core.model_runtime.adapters.openai_compatible import OpenAICompatibleAdapter
from core.model_runtime.adapters.qwen import QwenAdapter
from core.model_runtime.contracts import ModelPresetConfig, ModelRuntimeConfig, ReasoningEffort
from core.model_runtime.registry import ProviderRegistry, global_provider_registry
from core.model_runtime.runtime import ModelCandidate, ResilientChatModel


from core.model_runtime.reporters import ModelUsageReporterSlot
from core.model_runtime.usage import ModelIdentity
from utils.tokens import rough_estimation_for_messages


def _register_builtins(registry: ProviderRegistry) -> None:
    if "deepseek" not in registry.names:
        registry.register("deepseek", DeepSeekAdapter)
    if "openai_compatible" not in registry.names:
        registry.register("openai_compatible", OpenAICompatibleAdapter)
    if "qwen" not in registry.names:
        registry.register("qwen", QwenAdapter)
    if "kimi" not in registry.names:
        registry.register("kimi", KimiAdapter)
    if "glm" not in registry.names:
        registry.register("glm", GLMAdapter)


class ModelFactory:
    def __init__(
        self,
        config: ModelRuntimeConfig,
        registry: ProviderRegistry | None = None,
        reporter_slot: ModelUsageReporterSlot | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or global_provider_registry
        _register_builtins(self.registry)
        self.reporter_slot = reporter_slot or ModelUsageReporterSlot()
        self._cache: dict[tuple[str, ...], ResilientChatModel] = {}
        self._dotenv = dotenv_values(BASE_DIR / ".env")

    @classmethod
    def from_settings(cls) -> "ModelFactory":
        return cls(cls.config_from_raw(settings._config))

    @staticmethod
    def config_from_raw(raw: dict[str, Any]) -> ModelRuntimeConfig:
        return ModelRuntimeConfig.model_validate({
            "providers": raw.get("providers", {}),
            "models": raw.get("models", {}),
            "model_presets": raw.get("model_presets", {}),
            "model_routes": raw.get("model_routes", {}),
            "model_profiles": raw.get("model_profiles", {}),
            "default_model_profile": raw.get("defaults", {}).get("model_profile"),
        })

    def reload_from_settings(self) -> None:
        """Apply the latest runtime config and discard models built from old presets."""
        self.config = self.config_from_raw(settings._config)
        self._cache.clear()

    def _api_key(self, env_name: str) -> str:
        return str(os.environ.get(env_name) or getattr(settings, env_name, "") or self._dotenv.get(env_name) or "")

    def _candidate(
        self,
        preset_name: str,
        preset: ModelPresetConfig,
        thinking_enabled: bool | None = None,
    ) -> ModelCandidate:
        if thinking_enabled is False and preset.thinking.enabled:
            preset = preset.model_copy(update={
                "thinking": preset.thinking.model_copy(update={
                    "enabled": False,
                    "effort": ReasoningEffort.NONE,
                }),
            })
        definition = self.config.models[preset.model]
        if preset.thinking.enabled and not definition.capabilities.thinking:
            raise ValueError(f"Model {preset.model!r} does not support thinking")
        if preset.generation.max_output_tokens > definition.max_output_tokens:
            raise ValueError(f"Preset {preset_name!r} exceeds model output limit")
        provider = self.config.providers[definition.provider]
        api_key = self._api_key(provider.api_key_env)
        if not api_key:
            raise ValueError(
                f"Missing API key {provider.api_key_env} for provider {definition.provider!r}"
            )
        model = self.registry.build(
            provider.adapter,
            provider_name=definition.provider,
            provider=provider,
            model_name=preset.model,
            model=definition,
            preset_name=preset_name,
            preset=preset,
            api_key=api_key,
        )
        return ModelCandidate(
            preset_name=preset_name,
            provider_name=definition.provider,
            model_name=preset.model,
            definition=definition,
            preset=preset,
            model=model,
        )

    def build_route(
        self,
        route_name: str,
        *,
        model_profile: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> ResilientChatModel:
        entries = self.config.route_presets(route_name)
        key = ("route", route_name, str(model_profile), str(thinking_enabled), *(name for name, _ in entries))
        if key not in self._cache:
            self._cache[key] = ResilientChatModel(
                [self._candidate(name, preset, thinking_enabled) for name, preset in entries],
                model_profile=model_profile,
                route=route_name,
                reporter_slot=self.reporter_slot,
            )
        return self._cache[key]

    def build_preset(
        self,
        preset_name: str,
        *,
        model_profile: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> ResilientChatModel:
        key = ("preset", preset_name, str(model_profile), str(thinking_enabled))
        if key not in self._cache:
            preset = self.config.preset(preset_name)
            self._cache[key] = ResilientChatModel(
                [self._candidate(preset_name, preset, thinking_enabled)],
                model_profile=model_profile,
                route=None,
                reporter_slot=self.reporter_slot,
            )
        return self._cache[key]

    def build_profile_role(
        self,
        profile_name: str,
        role: str,
        *,
        thinking_enabled: bool | None = None,
    ) -> ResilientChatModel:
        preset_name = self.profile_preset(profile_name, role)
        return self.build_preset(
            preset_name,
            model_profile=profile_name,
            thinking_enabled=thinking_enabled,
        )

    def build_override(
        self,
        requested: str,
        *,
        base_route: str = "worker",
        model_profile: str | None = None,
        thinking_enabled: bool | None = None,
    ) -> ResilientChatModel:
        if requested in self.config.model_presets:
            return self.build_preset(
                requested,
                model_profile=model_profile,
                thinking_enabled=thinking_enabled,
            )
        if requested not in self.config.models:
            raise KeyError(
                f"Unknown model preset/model {requested!r}; presets={sorted(self.config.model_presets)}"
            )
        base_name, base = self.config.route_presets(base_route)[0]
        override = base.model_copy(update={"model": requested})
        key = ("override", base_name, requested, str(model_profile), str(thinking_enabled))
        if key not in self._cache:
            self._cache[key] = ResilientChatModel(
                [self._candidate(f"{base_name}@{requested}", override, thinking_enabled)],
                model_profile=model_profile,
                route=base_route,
                reporter_slot=self.reporter_slot,
            )
        return self._cache[key]

    def profile_identity(self, profile_name: str, role: str) -> ModelIdentity:
        profile = self.config.profile(profile_name)
        if role not in {"coordinator", "worker", "utility"}:
            raise KeyError(f"Unknown model profile role {role!r}")
        preset_name = str(getattr(profile, role))
        preset = self.config.preset(preset_name)
        model = self.config.models[preset.model]
        return ModelIdentity(
            provider=model.provider,
            provider_model=model.model_id,
            model_profile=profile_name,
            preset=preset_name,
            route=None,
            pricing_key=model.pricing_key,
            context_window_tokens=model.context_window_tokens,
            max_output_tokens=preset.generation.max_output_tokens,
        )

    def estimate_input_tokens(
        self,
        model_profile: str,
        messages: list[object],
    ) -> int | None:
        self.config.profile(model_profile)
        try:
            return rough_estimation_for_messages(messages)
        except Exception:
            return None

    def profile_preset(self, profile_name: str, role: str) -> str:
        profile = self.config.profile(profile_name)
        if role not in {"coordinator", "worker", "utility"}:
            raise KeyError(f"Unknown model profile role {role!r}")
        return str(getattr(profile, role))

    def profile_available(self, profile_name: str) -> bool:
        profile = self.config.profile(profile_name)
        provider = self.config.providers[profile.provider]
        return bool(self._api_key(provider.api_key_env))

    def public_profiles(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "label": profile.label,
                "provider": profile.provider,
                "available": self.profile_available(name),
            }
            for name, profile in self.config.model_profiles.items()
        }

    def route_primary_details(self, route_name: str) -> dict[str, Any]:
        preset_name, preset = self.config.route_presets(route_name)[0]
        model = self.config.models[preset.model]
        provider = self.config.providers[model.provider]
        return {
            "preset": preset_name,
            "model_name": preset.model,
            "model_id": model.model_id,
            "provider": model.provider,
            "base_url": provider.base_url,
            "context_window_tokens": model.context_window_tokens,
            "output_reserve_tokens": preset.generation.max_output_tokens,
            "thinking_enabled": preset.thinking.enabled,
            "reasoning_effort": preset.thinking.effort.value,
        }


_global_model_factory: ModelFactory | None = None


def get_global_model_factory() -> ModelFactory:
    global _global_model_factory
    if _global_model_factory is None:
        _global_model_factory = ModelFactory.from_settings()
    return _global_model_factory
