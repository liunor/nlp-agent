import pytest

from scripts.smoke_worker_kv_cache import _verify_cache_sequence


def _usage(provider: str, model: str, cached: int) -> dict[str, int | str]:
    return {
        "provider": provider,
        "model": model,
        "input_tokens": 100,
        "cached_input_tokens": cached,
    }


def test_worker_kv_cache_smoke_requires_common_prefix_hits_in_discovery_and_verification():
    _verify_cache_sequence(
        _usage("deepseek", "deepseek-chat", 0),
        _usage("deepseek", "deepseek-chat", 64),
        _usage("deepseek", "deepseek-chat", 70),
        stable_prefix_tokens=64,
    )


def test_worker_kv_cache_smoke_rejects_a_non_matching_cached_prefix():
    with pytest.raises(AssertionError, match="prefix discovery"):
        _verify_cache_sequence(
            _usage("deepseek", "deepseek-chat", 0),
            _usage("deepseek", "deepseek-chat", 1),
            _usage("deepseek", "deepseek-chat", 70),
            stable_prefix_tokens=64,
        )
