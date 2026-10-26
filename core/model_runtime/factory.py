"""Build resilient model routes from explicit typed configuration."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

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
from core.model_runtime.snapshot import ModelExecutionSnapshot, SnapshotPreset
from core.model_runtime.thinking import (
    THINKING_MAPPING_VERSION, ThinkingLevel, UnsupportedThinkingLevelError, resolve_thinking_config,
)
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

    def snapshot(
        self, *, profile_name: str, level: ThinkingLevel | str, user_id: str,
        workspace_id: str, session_id: str, turn_id: str,
    ) -> ModelExecutionSnapshot:
        """Freeze effective ordinary parameters and the fixed system policies."""

        selected = ThinkingLevel(level)
        profile = self.config.profile(profile_name)
        worker_override = settings.NLP_AGENT_WORKER_MODEL.strip()

        def ordinary(role: str) -> tuple[SnapshotPreset, ...]:
            primary = getattr(profile, role)
            overrides: dict[str, ModelPresetConfig] = {}
            if role == "worker" and worker_override:
                if worker_override in self.config.model_presets:
                    primary = worker_override
                elif worker_override in self.config.models:
                    base_name, base = self.config.route_presets("worker")[0]
                    primary = f"{base_name}@{worker_override}"
                    overrides[primary] = base.model_copy(update={"model": worker_override})
                else:
                    raise ValueError(f"Unknown global Worker model {worker_override!r}")
            names = [primary]
            route = self.config.model_routes.get(role)
            if route and route.primary == primary:
                names.extend(route.fallbacks)
            result: list[SnapshotPreset] = []
            for name in names:
                preset = overrides[name] if name in overrides else self.config.preset(name)
                model = self.config.models[preset.model]
                provider = self.config.providers[model.provider]
                try:
                    if preset.generation.max_output_tokens > model.max_output_tokens:
                        raise UnsupportedThinkingLevelError("Worker override exceeds model output limit")
                    if preset.native_search.enabled:
                        raise UnsupportedThinkingLevelError("Native search is a fixed system policy")
                    thinking = resolve_thinking_config(provider.adapter, model.model_id, selected)
                    if thinking.enabled and not model.capabilities.thinking:
                        raise UnsupportedThinkingLevelError("Model thinking capability is disabled")
                    if provider.adapter not in self.registry.names or (name != primary and not self._api_key(provider.api_key_env)):
                        raise UnsupportedThinkingLevelError("Model credentials or adapter are unavailable")
                except UnsupportedThinkingLevelError:
                    if name == primary:
                        raise
                    continue
                result.append(SnapshotPreset(preset_name=name, preset=preset.model_copy(update={"thinking": thinking})))
            return tuple(result)

        coordinator, worker = ordinary("coordinator"), ordinary("worker")
        raw = self.config.model_dump(mode="json")
        for provider in raw["providers"].values():
            endpoint = urlsplit(provider["base_url"])
            if endpoint.username or endpoint.password or endpoint.query:
                raise ValueError("Provider endpoints must not embed credentials or query parameters")
            provider["default_headers"] = {}
        return ModelExecutionSnapshot(
            user_id=user_id, workspace_id=workspace_id, session_id=session_id, turn_id=turn_id,
            profile_name=profile_name, level=selected, mapping_version=THINKING_MAPPING_VERSION,
            worker_override=bool(worker_override),
            runtime_json=json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            coordinator=coordinator, worker=worker,
            utility=(SnapshotPreset(preset_name=profile.utility, preset=self.config.preset(profile.utility)),),
            system_worker=(SnapshotPreset(preset_name=profile.worker, preset=self.config.preset(profile.worker)),),
        )

    def _snapshot_factory(self, snapshot: ModelExecutionSnapshot) -> "ModelFactory":
        config = ModelRuntimeConfig.model_validate_json(snapshot.runtime_json)
        providers = {}
        for name, provider in config.providers.items():
            current = self.config.providers.get(name)
            providers[name] = provider.model_copy(update={"default_headers": current.default_headers if current else {}})
        return ModelFactory(config.model_copy(update={"providers": providers}), registry=self.registry, reporter_slot=self.reporter_slot)

    def build_snapshot_role(
        self, snapshot: ModelExecutionSnapshot, role: str, *, preset_name: str | None = None,
    ) -> ResilientChatModel:
        entries = snapshot.role_presets(role)
        if preset_name is not None:
            entries = tuple(item for item in entries if item.preset_name == preset_name)
            if not entries:
                raise ValueError("Requested preset is outside this Turn execution snapshot")
        key = (("snapshot", snapshot.execution_key(), role, "preset", preset_name)
               if preset_name is not None else ("snapshot", snapshot.execution_key(), role, "route"))
        if key not in self._cache:
            frozen = self._snapshot_factory(snapshot)
            self._cache[key] = ResilientChatModel(
                [frozen._candidate(item.preset_name, item.preset) for item in entries],
                model_profile=snapshot.profile_name, route=role, reporter_slot=self.reporter_slot,
            )
        return self._cache[key]

    def build_snapshot_system_preset(self, snapshot: ModelExecutionSnapshot, preset_name: str) -> ResilientChatModel:
        key = ("snapshot-system", snapshot.execution_key(), preset_name)
        if key not in self._cache:
            frozen = self._snapshot_factory(snapshot)
            self._cache[key] = frozen.build_preset(preset_name, model_profile=snapshot.profile_name)
        return self._cache[key]

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
        primary = self.config.preset(preset_name)
        entries = [(preset_name, primary)]
        route = self.config.model_routes.get(role)
        if route is not None and route.primary == preset_name:
            # Profiles pin the Provider. Only reuse an explicitly configured
            # route for the same primary; never import another profile's route
            # or silently replace a specialized native-search preset.
            provider = self.config.profile(profile_name).provider
            entries.extend(
                (name, preset)
                for name, preset in self.config.route_presets(role)[1:]
                if self.config.models[preset.model].provider == provider
                and preset.native_search == primary.native_search
            )
        key = ("profile_role", profile_name, role, str(thinking_enabled), *(name for name, _ in entries))
        if key not in self._cache:
            self._cache[key] = ResilientChatModel(
                [self._candidate(name, preset, thinking_enabled) for name, preset in entries],
                model_profile=profile_name,
                route=None,
                reporter_slot=self.reporter_slot,
            )
        return self._cache[key]

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

    def profile_thinking_levels(self, profile_name: str) -> tuple[ThinkingLevel, ...]:
        from core.model_runtime.thinking import supported_profile_thinking_levels

        override = settings.NLP_AGENT_WORKER_MODEL.strip()
        if not override:
            return supported_profile_thinking_levels(self.config, profile_name)
        preset = self.config.model_presets.get(override)
        if preset is None:
            if override not in self.config.models:
                return ()
            _, base = self.config.route_presets("worker")[0]
            preset = base.model_copy(update={"model": override})
        model = self.config.models[preset.model]
        provider = self.config.providers[model.provider]
        if (preset.native_search.enabled or preset.generation.max_output_tokens > model.max_output_tokens
                or provider.adapter not in self.registry.names or not self._api_key(provider.api_key_env)):
            return ()
        profile = self.config.profile(profile_name).model_copy(update={"worker": "__effective_worker"})
        effective = self.config.model_copy(update={
            "model_presets": {**self.config.model_presets, "__effective_worker": preset},
            "model_profiles": {**self.config.model_profiles, profile_name: profile},
        })
        return supported_profile_thinking_levels(effective, profile_name)

    def public_profiles(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "label": profile.label,
                "provider": profile.provider,
                "available": self.profile_available(name),
                "supported_levels": [
                    level.value for level in self.profile_thinking_levels(name)
                ] if (
                    self.profile_available(name)
                    and self.config.providers[profile.provider].adapter in self.registry.names
                ) else [],
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
