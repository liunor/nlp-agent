from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def test_claim_nonce_is_hashed_and_fences_reuse() -> None:
    from server.sandbox.warm_pool import claim_nonce_hash, validate_claim_nonce

    nonce = "one-time-secret"
    digest = claim_nonce_hash(nonce)

    assert digest == hashlib.sha256(nonce.encode()).hexdigest()
    assert validate_claim_nonce(digest, nonce)
    assert not validate_claim_nonce(digest, "other")


def test_runtime_state_transitions_never_return_a_used_runtime_to_ready_pool() -> None:
    from server.sandbox.warm_pool import RuntimeState, transition_runtime

    assert transition_runtime(RuntimeState.READY_UNBOUND, RuntimeState.CLAIMING) == RuntimeState.CLAIMING
    assert transition_runtime(RuntimeState.CLAIMING, RuntimeState.ASSIGNED) == RuntimeState.ASSIGNED
    assert transition_runtime(RuntimeState.ASSIGNED, RuntimeState.DRAINING) == RuntimeState.DRAINING
    assert transition_runtime(RuntimeState.DRAINING, RuntimeState.DESTROYED) == RuntimeState.DESTROYED


def test_warm_pool_slot_name_is_stable_and_does_not_contain_a_secret() -> None:
    from server.sandbox.warm_pool import runtime_container_name

    assert runtime_container_name("1f44c054-8f79-46ef-84ce-4fd9aaf0f8f7") == "nova-runtime-1f44c0548f7946ef84ce4fd9aaf0f8f7"


def test_pool_reconcile_decides_orphan_and_missing_runtime_actions() -> None:
    from server.sandbox.warm_pool import reconcile_runtime_ids

    action = reconcile_runtime_ids(
        database_ids={"db-a", "db-b"},
        docker_ids={"db-a", "orphan"},
        now=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    assert action.missing_database_ids == {"db-b"}
    assert action.orphan_docker_ids == {"orphan"}
