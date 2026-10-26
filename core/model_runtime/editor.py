"""Non-reasoning fields exposed by the developer model preset editor."""

from typing import Any

from core.model_runtime.contracts import (
    FrozenModel,
    GenerationConfig,
    NativeSearchConfig,
    TimeoutPolicy,
    RetryPolicy,
    CircuitBreakerPolicy,
)


class ModelPresetEdit(FrozenModel):
    model: str | None = None
    generation: GenerationConfig | None = None
    native_search: NativeSearchConfig | None = None
    timeouts: TimeoutPolicy | None = None
    retry: RetryPolicy | None = None
    circuit_breaker: CircuitBreakerPolicy | None = None


def editable_presets(presets: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            key: value
            for key, value in preset.items()
            if key in ModelPresetEdit.model_fields
        }
        for name, preset in presets.items()
    }
