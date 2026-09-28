"""End-to-end behavior and authorization checks for quota HTTP operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from ..support.http import json_response, problem_response


pytestmark = pytest.mark.api_full


def _window() -> tuple[str, str, str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    period_start = now - timedelta(hours=1)
    period_end = now + timedelta(days=1)
    effective_from = now - timedelta(minutes=1)
    effective_until = now + timedelta(days=2)
    return tuple(
        value.isoformat()
        for value in (period_start, period_end, effective_from, effective_until)
    )


def _policy_body(code: str, *, effective_from: str, name: str = "HTTP quota policy") -> dict:
    return {
        "code": code,
        "version": "v1",
        "name": name,
        "request_limit_micro": 100_000,
        "daily_limit_micro": 100_000,
        "weekly_limit_micro": 700_000,
        "concurrency_limit": 2,
        "max_overdraft_micro": 0,
        "allowed_model_profiles": ["default"],
        "unlimited": False,
        "effective_from": effective_from,
        "status": "draft",
    }


def _pricing_body(pricing_key: str, *, effective_from: str) -> dict:
    return {
        "pricing_key": pricing_key,
        "version": "v1",
        "effective_from": effective_from,
        "ordinary_input_credits_micro_per_million_tokens": 1,
        "cached_input_credits_micro_per_million_tokens": 1,
        "cache_write_credits_micro_per_million_tokens": 1,
        "output_credits_micro_per_million_tokens": 2,
        "reasoning_output_credits_micro_per_million_tokens": 3,
    }


def _credit_body(
    owner_id: str,
    *,
    effective_from: str,
    period_start: str,
    period_end: str,
    amount: int = 100,
    idempotency_key: str | None = None,
) -> dict:
    return {
        "owner_type": "user",
        "owner_id": owner_id,
        "bucket_type": "daily",
        "period_start": period_start,
        "period_end": period_end,
        "amount_micro": amount,
        "reason": "real HTTP quota behavior test",
        "idempotency_key": idempotency_key or f"api-http-{uuid.uuid4().hex}",
        "effective_from": effective_from,
    }


def test_quota_policy_pricing_and_binding_lifecycles(
    authenticated_client_for,
    developer_user,
) -> None:
    client = authenticated_client_for(developer_user)
    period_start, period_end, effective_from, effective_until = _window()
    code = f"api.http.{uuid.uuid4().hex[:12]}"

    created = json_response(
        client.post(
            "/api/v1/developer/quota/policies",
            json=_policy_body(code, effective_from=effective_from),
        ),
        201,
    )
    assert created["code"] == code
    assert created["status"] == "draft"
    policy_id = created["policy_id"]

    listed = json_response(
        client.get(f"/api/v1/developer/quota/policies?code={code}"),
        200,
    )
    assert [item["policy_id"] for item in listed["items"]] == [policy_id]
    assert json_response(
        client.get(f"/api/v1/developer/quota/policies/{policy_id}"),
        200,
    )["name"] == "HTTP quota policy"

    updated = json_response(
        client.patch(
            f"/api/v1/developer/quota/policies/{policy_id}",
            json={"name": "Updated HTTP quota policy"},
        ),
        200,
    )
    assert updated["name"] == "Updated HTTP quota policy"

    published = json_response(
        client.post(f"/api/v1/developer/quota/policies/{policy_id}/publish"),
        200,
    )
    assert published["status"] == "active"
    immutable = client.patch(
        f"/api/v1/developer/quota/policies/{policy_id}",
        json={"name": "must remain immutable"},
    )
    assert immutable.status_code == 409, immutable.text
    problem_response(immutable, 409)

    binding = json_response(
        client.post(
            "/api/v1/developer/quota/bindings",
            json={
                "subject_type": "user",
                "subject_id": developer_user.user_id,
                "policy_id": policy_id,
                "priority": 10,
                "effective_from": effective_from,
                "effective_until": effective_until,
            },
        ),
        201,
    )
    binding_id = binding["binding_id"]
    assert binding["policy_id"] == policy_id
    assert json_response(
        client.get(f"/api/v1/developer/quota/bindings/{binding_id}"),
        200,
    )["binding_id"] == binding_id
    assert any(
        item["binding_id"] == binding_id
        for item in json_response(
            client.get(
                "/api/v1/developer/quota/bindings"
                f"?subject_type=user&subject_id={developer_user.user_id}"
            ),
            200,
        )["items"]
    )
    retired_binding = json_response(
        client.delete(f"/api/v1/developer/quota/bindings/{binding_id}"),
        200,
    )
    assert retired_binding["status"] == "retired"

    archived_policy = client.post(
        f"/api/v1/developer/quota/policies/{policy_id}/archive"
    )
    assert problem_response(archived_policy, 409)["code"] == "quota_policy_conflict"

    pricing_key = f"api.http.pricing.{uuid.uuid4().hex[:8]}"
    pricing = json_response(
        client.post(
            "/api/v1/developer/quota/pricing-rules",
            json=_pricing_body(pricing_key, effective_from=effective_from),
        ),
        201,
    )
    pricing_id = pricing["pricing_rule_id"]
    assert json_response(
        client.get(f"/api/v1/developer/quota/pricing-rules/{pricing_id}"),
        200,
    )["pricing_key"] == pricing_key
    assert any(
        item["pricing_rule_id"] == pricing_id
        for item in json_response(
            client.get(
                f"/api/v1/developer/quota/pricing-rules?pricing_key={pricing_key}"
            ),
            200,
        )["items"]
    )
    retired_pricing = json_response(
        client.delete(f"/api/v1/developer/quota/pricing-rules/{pricing_id}"),
        200,
    )
    assert retired_pricing["status"] == "retired"

    missing = client.get(
        f"/api/v1/developer/quota/policies/{uuid.uuid4()}"
    )
    assert missing.status_code == 409
    assert problem_response(missing, 409)["code"] == "quota_policy_not_found"


def test_quota_grant_adjustment_credit_and_read_models_are_consistent(
    authenticated_client_for,
    developer_user,
) -> None:
    client = authenticated_client_for(developer_user)
    period_start, period_end, effective_from, _effective_until = _window()

    grant = json_response(
        client.post(
            "/api/v1/developer/quota/grants",
            json={
                "owner_type": "user",
                "owner_id": developer_user.user_id,
                "bucket_type": "daily",
                "period_start": period_start,
                "period_end": period_end,
                "allocated_micro": 500,
                "source_type": "grant",
                "reason": "real HTTP grant",
                "idempotency_key": f"api-http-grant-{uuid.uuid4().hex}",
                "effective_from": effective_from,
            },
        ),
        201,
    )
    grant_id = grant["grant_id"]
    assert grant["allocated_micro"] == 500
    assert json_response(
        client.get(f"/api/v1/developer/quota/grants/{grant_id}"),
        200,
    )["grant_id"] == grant_id
    assert any(
        item["grant_id"] == grant_id
        for item in json_response(
            client.get(
                "/api/v1/developer/quota/grants"
                f"?owner_type=user&owner_id={developer_user.user_id}"
            ),
            200,
        )["items"]
    )

    adjustment = json_response(
        client.post(
            "/api/v1/developer/quota/adjustments",
            json={
                "owner_type": "user",
                "owner_id": developer_user.user_id,
                "bucket_type": "daily",
                "period_start": period_start,
                "period_end": period_end,
                "amount_micro": 75,
                "reason": "real HTTP adjustment",
                "idempotency_key": f"api-http-adjustment-{uuid.uuid4().hex}",
            },
        ),
        201,
    )
    adjustment_id = adjustment["adjustment_id"]
    assert json_response(
        client.get(f"/api/v1/developer/quota/adjustments/{adjustment_id}"),
        200,
    )["adjustment_id"] == adjustment_id
    assert any(
        item["adjustment_id"] == adjustment_id
        for item in json_response(
            client.get(
                "/api/v1/developer/quota/adjustments"
                f"?owner_type=user&owner_id={developer_user.user_id}"
            ),
            200,
        )["items"]
    )

    # A reset intentionally expires every other active grant in the same
    # owner-period scope.  Revoke this standalone grant before exercising that
    # replacement behavior so the revoke contract is tested against an active
    # grant rather than against the reset state.
    revoked = json_response(
        client.post(
            f"/api/v1/developer/quota/grants/{grant_id}/revoke",
            json={"idempotency_key": f"api-http-revoke-{uuid.uuid4().hex}"},
        ),
        200,
    )
    assert revoked["status"] == "revoked"

    operation_key = f"api-http-credit-{uuid.uuid4().hex}"
    gift = json_response(
        client.post(
            "/api/v1/developer/quota/credits/gift",
            json=_credit_body(
                developer_user.user_id,
                effective_from=effective_from,
                period_start=period_start,
                period_end=period_end,
                idempotency_key=operation_key,
            ),
        ),
        201,
    )
    assert gift["operation_type"] == "gift"
    replay = json_response(
        client.post(
            "/api/v1/developer/quota/credits/gift",
            json=_credit_body(
                developer_user.user_id,
                effective_from=effective_from,
                period_start=period_start,
                period_end=period_end,
                idempotency_key=operation_key,
            ),
        ),
        201,
    )
    assert replay["operation_id"] == gift["operation_id"]

    role_gift = json_response(
        client.post(
            "/api/v1/developer/quota/credits/gift-role",
            json={
                "role_code": "student",
                "bucket_type": "daily",
                "period_start": period_start,
                "period_end": period_end,
                "amount_micro": 25,
                "reason": "real HTTP role credit",
                "idempotency_key": f"api-http-role-{uuid.uuid4().hex}",
                "effective_from": effective_from,
            },
        ),
        201,
    )
    assert role_gift["target_id"] == "student"
    reset = json_response(
        client.post(
            "/api/v1/developer/quota/credits/reset",
            json=_credit_body(
                developer_user.user_id,
                effective_from=effective_from,
                period_start=period_start,
                period_end=period_end,
                amount=900,
                idempotency_key=f"api-http-reset-{uuid.uuid4().hex}",
            ),
        ),
        201,
    )
    assert reset["operation_type"] == "reset"
    assert any(
        item["operation_id"] in {gift["operation_id"], reset["operation_id"]}
        for item in json_response(
            client.get("/api/v1/developer/quota/credits?limit=100"),
            200,
        )["items"]
    )

    rollups = json_response(
        client.get("/api/v1/developer/quota/daily-rollups?start=2026-01-01&end=2026-01-02"),
        200,
    )
    assert isinstance(rollups["items"], list)
    buckets = json_response(
        client.get(
            "/api/v1/developer/quota/buckets"
            f"?owner_type=user&owner_id={developer_user.user_id}&limit=100"
        ),
        200,
    )
    assert isinstance(buckets["items"], list)


def test_quota_billing_archive_alert_and_not_found_contracts(
    authenticated_client_for,
    developer_user,
) -> None:
    client = authenticated_client_for(developer_user)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    reconciled = json_response(
        client.post(
            "/api/v1/developer/quota/billing/reconcile",
            json={
                "statements": [
                    {
                        "provider": "deterministic",
                        "statement_id": f"statement-{uuid.uuid4().hex}",
                        "operation_id": f"operation-{uuid.uuid4().hex}",
                        "billed_at": now,
                        "billed_credits_micro": 10,
                        "billed_tokens": {"total_tokens": 10},
                        "idempotency_key": f"api-http-billing-{uuid.uuid4().hex}",
                    }
                ]
            },
        ),
        200,
    )
    assert reconciled["total"] == 1
    assert sum(reconciled[key] for key in ("matched", "discrepancies", "unmatched", "pending")) == 1

    billing = json_response(
        client.get("/api/v1/developer/quota/billing?limit=100"),
        200,
    )
    assert any(item["operation_id"] == reconciled["items"][0]["operation_id"] for item in billing["items"])

    archive = json_response(
        client.post(
            "/api/v1/developer/quota/archive",
            json={"before": "2020-01-01T00:00:00Z", "batch_size": 100},
        ),
        200,
    )
    assert archive["archived_events"] >= 0
    batches = json_response(
        client.get("/api/v1/developer/quota/archive?limit=100"),
        200,
    )
    assert isinstance(batches["items"], list)
    purged = json_response(
        client.post(
            "/api/v1/developer/quota/archive/purge",
            json={"before": "2020-01-01T00:00:00Z", "batch_size": 100},
        ),
        200,
    )
    assert purged["deleted_events"] >= 0

    alerts = json_response(
        client.get("/api/v1/developer/quota/alerts?limit=100"),
        200,
    )
    assert isinstance(alerts["items"], list)
    missing_alert = client.patch(
        f"/api/v1/developer/quota/alerts/{uuid.uuid4()}",
        json={"status": "resolved", "reason": "not found contract"},
    )
    assert missing_alert.status_code == 404
    assert problem_response(missing_alert, 404)["code"] == "quota_alert_not_found"

    for path in (
        f"/api/v1/developer/quota/buckets/{uuid.uuid4()}/replay",
        f"/api/v1/developer/quota/buckets/{uuid.uuid4()}/repair",
        f"/api/v1/developer/quota/billing/{uuid.uuid4()}/repair",
    ):
        method = "post" if path.endswith("/repair") else "get"
        if method == "post":
            response = client.post(
                path,
                json={"reason": "not found contract", "idempotency_key": str(uuid.uuid4())},
            )
        else:
            response = client.get(path)
        assert response.status_code == 404, response.text
