"""Provider-neutral model construction and resilient execution."""

from core.model_runtime.contracts import ModelRuntimeConfig

__all__ = ["ModelFactory", "ModelRuntimeConfig", "get_global_model_factory"]


def __getattr__(name: str):
    if name in {"ModelFactory", "get_global_model_factory"}:
        from core.model_runtime.factory import ModelFactory, get_global_model_factory

        return {"ModelFactory": ModelFactory, "get_global_model_factory": get_global_model_factory}[name]
    raise AttributeError(name)
