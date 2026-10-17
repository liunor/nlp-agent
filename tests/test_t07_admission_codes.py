"""Stable HTTP admission error codes for database-trigger rejections."""

from types import SimpleNamespace
from unittest.mock import patch
import pytest

from gateway.contracts import AdmissionLimitError
from gateway.core import _estimate_turn_reservation_tokens
from gateway.mysql_repository import MySQLGatewayRepository


def test_mysql_user_active_limit_trigger_maps_to_public_turn_limit_code():
    error = SimpleNamespace(
        orig=SimpleNamespace(args=(1644, "user_turn_limit_reached"))
    )

    assert MySQLGatewayRepository._admission_limit_code(error) == "user_turn_limit_reached"


def test_unrelated_integrity_signal_is_not_misreported_as_admission_limit():
    error = SimpleNamespace(
        orig=SimpleNamespace(args=(1644, "model_quota_reservation_underflow"))
    )

    assert MySQLGatewayRepository._admission_limit_code(error) is None


def test_model_quota_limit_uses_the_stable_public_problem_code():
    assert AdmissionLimitError.MODEL_QUOTA_EXCEEDED == "quota_admission_denied"


@pytest.mark.parametrize("estimated", [None, 1, 100])
def test_reservation_covers_full_prompt_even_when_current_text_estimate_is_small(estimated):
    factory = SimpleNamespace(
        config=SimpleNamespace(
            default_model_profile="classroom",
            model_presets={
                "coordinator": SimpleNamespace(
                    model="large-context", generation=SimpleNamespace(max_output_tokens=256)
                )
            },
            models={"large-context": SimpleNamespace(context_window_tokens=32_000)},
        ),
        profile_preset=lambda _profile, _role: "coordinator",
        estimate_input_tokens=lambda _profile, _messages: estimated,
    )

    with patch("core.model_runtime.factory.get_global_model_factory", return_value=factory):
        assert _estimate_turn_reservation_tokens("short input", "classroom") == 32_256


def test_runtime_metrics_use_durable_running_count_and_worker_heartbeats():
    from gateway.core import active_turn_count, active_worker_slot_count

    assert active_turn_count({"running_turns": 2}, fallback=99) == 2
    assert active_turn_count(None, fallback=7) == 7
    assert active_worker_slot_count(
        [{"active_slots": 2}, {"active_slots": 1}]
    ) == 3
    assert active_worker_slot_count(None) is None


def test_quota_ledger_models_include_window_settlement_and_pricing_fields():
    from server.infrastructure.mysql.models import (
        ModelQuotaAccountModel,
        ModelQuotaReservationModel,
        ModelUsageEventModel,
    )
    from configs.settings import Settings

    assert {
        "window_started_at", "window_ends_at", "token_limit", "used_tokens"
    } <= set(ModelQuotaAccountModel.__table__.columns.keys())
    assert {
        "model_profile", "settled_tokens", "expires_at"
    } <= set(ModelQuotaReservationModel.__table__.columns.keys())
    assert {"pricing_version", "charge_units"} <= set(
        ModelUsageEventModel.__table__.columns.keys()
    )
    assert "NLP_AGENT_TEST_MODEL_QUOTA_TOKENS" in Settings.model_fields
