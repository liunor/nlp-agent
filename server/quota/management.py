"""Developer-owned quota policy, grant, and adjustment management."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import Engine, and_, create_engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from server.quota.contracts import AdmitTurn, PolicyBinding, QuotaPolicy
from server.quota.errors import QuotaDomainError, QuotaErrorCode
from server.quota.models import (
    PolicyBindingModel,
    QuotaAdjustmentModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    PricingRuleModel,
)
from server.quota.policy import resolve_effective_policy
from server.quota.pricing import PricingCatalog, PricingRule


UTC = timezone.utc
_OWNER_TYPES = {"user", "workspace", "classroom"}
_BUCKET_TYPES = {"daily", "weekly"}
_SOURCE_TYPES = {"role", "purchase", "grant", "adjustment", "reset"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("quota timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _db_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=UTC).isoformat() if value is not None else None


def _validate_period(
    *, owner_type: str, bucket_type: str, period_start: datetime, period_end: datetime
) -> tuple[datetime, datetime]:
    if owner_type not in _OWNER_TYPES:
        raise ValueError(f"unsupported quota owner_type: {owner_type}")
    if bucket_type not in _BUCKET_TYPES:
        raise ValueError(f"unsupported quota bucket_type: {bucket_type}")
    start = _utc(period_start)
    end = _utc(period_end)
    if end <= start:
        raise ValueError("period_end must be after period_start")
    return start, end


class QuotaManagementService:
    """The only write service for developer-managed quota configuration.

    Policy and allocation records are append-oriented.  A later version or
    adjustment never edits a historical reservation or ledger snapshot; it
    only changes the effective read-side policy/capacity for future requests.
    """

    def __init__(self, database: str | Engine) -> None:
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        else:
            self._engine = database
            self._owns_engine = False

    @property
    def engine(self) -> Engine:
        return self._engine

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    @staticmethod
    def _validate_policy_values(
        *,
        code: str,
        version: str,
        name: str,
        request_limit_micro: int | None,
        daily_limit_micro: int | None,
        weekly_limit_micro: int | None,
        concurrency_limit: int | None,
        max_overdraft_micro: int,
    ) -> None:
        if not code or not version or not name:
            raise ValueError("policy code, version, and name are required")
        for field_name, value in (
            ("request_limit_micro", request_limit_micro),
            ("daily_limit_micro", daily_limit_micro),
            ("weekly_limit_micro", weekly_limit_micro),
            ("concurrency_limit", concurrency_limit),
            ("max_overdraft_micro", max_overdraft_micro),
        ):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def create_policy(
        self,
        *,
        code: str,
        version: str,
        name: str,
        request_limit_micro: int | None = None,
        daily_limit_micro: int | None = None,
        weekly_limit_micro: int | None = None,
        concurrency_limit: int | None = None,
        max_overdraft_micro: int = 0,
        allowed_model_profiles: Sequence[str] = (),
        unlimited: bool = False,
        effective_from: datetime,
        effective_until: datetime | None = None,
        created_by: str,
        status: str = "draft",
    ) -> dict[str, Any]:
        effective_from = _utc(effective_from)
        if effective_until is not None:
            effective_until = _utc(effective_until)
            if effective_until <= effective_from:
                raise ValueError("effective_until must be after effective_from")
        self._validate_policy_values(
            code=code,
            version=version,
            name=name,
            request_limit_micro=request_limit_micro,
            daily_limit_micro=daily_limit_micro,
            weekly_limit_micro=weekly_limit_micro,
            concurrency_limit=concurrency_limit,
            max_overdraft_micro=max_overdraft_micro,
        )
        if status not in {"draft", "active"}:
            raise ValueError("policy status must be draft or active")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(QuotaPolicyModel).where(
                    QuotaPolicyModel.code == code,
                    QuotaPolicyModel.version == version,
                )
            ).mappings().first()
            if existing is not None:
                raise QuotaDomainError(
                    QuotaErrorCode.POLICY_VERSION_CONFLICT,
                    f"Policy {code!r} version {version!r} already exists",
                )
            policy_id = str(uuid.uuid4())
            connection.execute(
                insert(QuotaPolicyModel).values(
                    id=policy_id,
                    code=code,
                    version=version,
                    name=name,
                    status=status,
                    request_limit_micro=request_limit_micro,
                    daily_limit_micro=daily_limit_micro,
                    weekly_limit_micro=weekly_limit_micro,
                    concurrency_limit=concurrency_limit,
                    max_overdraft_micro=max_overdraft_micro,
                    allowed_model_profiles=list(allowed_model_profiles),
                    unlimited=unlimited,
                    effective_from=_db_time(effective_from),
                    effective_until=_db_time(effective_until) if effective_until else None,
                    created_by=created_by,
                )
            )
            row = connection.execute(
                select(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            ).mappings().one()
        return self._policy_payload(row)

    def publish_policy(self, policy_id: str, *, actor_user_id: str) -> dict[str, Any]:
        with self._engine.begin() as connection:
            policy = connection.execute(
                select(QuotaPolicyModel)
                .where(QuotaPolicyModel.id == policy_id)
                .with_for_update()
            ).mappings().first()
            if policy is None:
                raise QuotaDomainError(QuotaErrorCode.POLICY_NOT_FOUND, "Policy does not exist")
            if policy["status"] == "active":
                return self._policy_payload(policy)
            if policy["status"] != "draft":
                raise QuotaDomainError(
                    QuotaErrorCode.POLICY_CONFLICT,
                    "Only a draft policy can be published",
                )
            # Publishing makes this immutable version eligible for a new
            # binding. Existing bindings continue to point at their prior
            # version until the developer explicitly publishes a replacement
            # binding, so a policy publish cannot cause an admission outage.
            connection.execute(
                update(QuotaPolicyModel)
                .where(QuotaPolicyModel.id == policy_id)
                .values(status="active")
            )
            row = connection.execute(
                select(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            ).mappings().one()
        return {**self._policy_payload(row), "published_by": actor_user_id}

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            ).mappings().first()
        if row is None:
            raise QuotaDomainError(QuotaErrorCode.POLICY_NOT_FOUND, "Policy does not exist")
        return self._policy_payload(row)

    def update_policy(
        self,
        policy_id: str,
        *,
        actor_user_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        allowed = {
            "code",
            "version",
            "name",
            "request_limit_micro",
            "daily_limit_micro",
            "weekly_limit_micro",
            "concurrency_limit",
            "max_overdraft_micro",
            "allowed_model_profiles",
            "unlimited",
            "effective_from",
            "effective_until",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported policy fields: {', '.join(sorted(unknown))}")
        with self._engine.begin() as connection:
            row = connection.execute(
                select(QuotaPolicyModel)
                .where(QuotaPolicyModel.id == policy_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise QuotaDomainError(QuotaErrorCode.POLICY_NOT_FOUND, "Policy does not exist")
            if row["status"] != "draft":
                raise QuotaDomainError(
                    QuotaErrorCode.POLICY_CONFLICT,
                    "Published policies are immutable; create a new version instead",
                )
            merged = {
                key: row[key]
                for key in (
                    "code",
                    "version",
                    "name",
                    "request_limit_micro",
                    "daily_limit_micro",
                    "weekly_limit_micro",
                    "concurrency_limit",
                    "max_overdraft_micro",
                    "allowed_model_profiles",
                    "unlimited",
                    "effective_from",
                    "effective_until",
                )
            }
            merged.update(changes)
            effective_from = self._stored_utc(merged["effective_from"])
            effective_until = merged["effective_until"]
            if effective_until is not None:
                effective_until = self._stored_utc(effective_until)
                if effective_until <= effective_from:
                    raise ValueError("effective_until must be after effective_from")
            self._validate_policy_values(
                code=merged["code"],
                version=merged["version"],
                name=merged["name"],
                request_limit_micro=merged["request_limit_micro"],
                daily_limit_micro=merged["daily_limit_micro"],
                weekly_limit_micro=merged["weekly_limit_micro"],
                concurrency_limit=merged["concurrency_limit"],
                max_overdraft_micro=merged["max_overdraft_micro"],
            )
            values = {
                **merged,
                "effective_from": _db_time(effective_from),
                "effective_until": _db_time(effective_until) if effective_until else None,
                "updated_at": _db_time(datetime.now(UTC)),
            }
            try:
                connection.execute(
                    update(QuotaPolicyModel)
                    .where(QuotaPolicyModel.id == policy_id)
                    .values(**values)
                )
            except IntegrityError as error:
                raise QuotaDomainError(
                    QuotaErrorCode.POLICY_VERSION_CONFLICT,
                    "Policy code and version already exist",
                ) from error
            updated = connection.execute(
                select(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            ).mappings().one()
        return {**self._policy_payload(updated), "updated_by": actor_user_id}

    def archive_policy(self, policy_id: str, *, actor_user_id: str) -> dict[str, Any]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(QuotaPolicyModel)
                .where(QuotaPolicyModel.id == policy_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise QuotaDomainError(QuotaErrorCode.POLICY_NOT_FOUND, "Policy does not exist")
            if row["status"] == "archived":
                return self._policy_payload(row)
            if row["status"] != "draft":
                raise QuotaDomainError(
                    QuotaErrorCode.POLICY_CONFLICT,
                    "Only a draft policy can be archived; published versions are immutable",
                )
            connection.execute(
                update(QuotaPolicyModel)
                .where(QuotaPolicyModel.id == policy_id)
                .values(status="archived", updated_at=_db_time(datetime.now(UTC)))
            )
            archived = connection.execute(
                select(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            ).mappings().one()
        return {**self._policy_payload(archived), "archived_by": actor_user_id}

    def list_policies(self, *, code: str | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaPolicyModel).order_by(
                QuotaPolicyModel.code, QuotaPolicyModel.effective_from.desc(), QuotaPolicyModel.version
            )
            if code:
                statement = statement.where(QuotaPolicyModel.code == code)
            return [self._policy_payload(row) for row in connection.execute(statement).mappings()]

    def create_pricing_rule(
        self,
        *,
        pricing_key: str,
        version: str,
        effective_from: datetime,
        effective_until: datetime | None,
        ordinary_input_credits_micro_per_million_tokens: int,
        cached_input_credits_micro_per_million_tokens: int,
        cache_write_credits_micro_per_million_tokens: int,
        output_credits_micro_per_million_tokens: int,
        reasoning_output_credits_micro_per_million_tokens: int | None,
        created_by: str,
        visual_input_credits_micro_per_million_tokens: int | None = None,
        image_unit_credits_micro: int | None = None,
        search_call_credits_micro: int | None = None,
        link_page_credits_micro: int | None = None,
    ) -> dict[str, Any]:
        """Create one active, immutable pricing version.

        Pricing is deliberately explicit configuration.  A missing rule must
        remain pending rather than becoming a free call, while overlapping
        version windows are rejected before they can make pricing ambiguous.
        """
        candidate = PricingRule(
            pricing_key=pricing_key,
            version=version,
            effective_from=_utc(effective_from),
            effective_until=_utc(effective_until) if effective_until else None,
            ordinary_input_credits_micro_per_million_tokens=(
                ordinary_input_credits_micro_per_million_tokens
            ),
            cached_input_credits_micro_per_million_tokens=(
                cached_input_credits_micro_per_million_tokens
            ),
            cache_write_credits_micro_per_million_tokens=(
                cache_write_credits_micro_per_million_tokens
            ),
            output_credits_micro_per_million_tokens=output_credits_micro_per_million_tokens,
            reasoning_output_credits_micro_per_million_tokens=(
                reasoning_output_credits_micro_per_million_tokens
            ),
            visual_input_credits_micro_per_million_tokens=(
                visual_input_credits_micro_per_million_tokens
            ),
            image_unit_credits_micro=image_unit_credits_micro,
            search_call_credits_micro=search_call_credits_micro,
            link_page_credits_micro=link_page_credits_micro,
        )
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(PricingRuleModel)
                .where(
                    PricingRuleModel.pricing_key == candidate.pricing_key,
                    PricingRuleModel.version == candidate.version,
                )
                .with_for_update()
            ).mappings().first()
            if existing is not None:
                raise QuotaDomainError(
                    QuotaErrorCode.PRICING_RULE_CONFLICT,
                    "Pricing rule key and version already exist",
                )
            existing_rows = connection.execute(
                select(PricingRuleModel)
                .where(PricingRuleModel.pricing_key == candidate.pricing_key)
                .with_for_update()
            ).mappings().all()
            try:
                PricingCatalog(
                    [self._pricing_rule_from_row(row) for row in existing_rows]
                    + [candidate]
                )
            except ValueError as error:
                raise QuotaDomainError(
                    QuotaErrorCode.PRICING_RULE_CONFLICT,
                    str(error),
                ) from error

            rule_id = str(uuid.uuid4())
            try:
                connection.execute(
                    insert(PricingRuleModel).values(
                        id=rule_id,
                        pricing_key=candidate.pricing_key,
                        version=candidate.version,
                        effective_from=_db_time(candidate.effective_from),
                        effective_until=(
                            _db_time(candidate.effective_until)
                            if candidate.effective_until
                            else None
                        ),
                        ordinary_input_credits_micro_per_million_tokens=(
                            candidate.ordinary_input_credits_micro_per_million_tokens
                        ),
                        cached_input_credits_micro_per_million_tokens=(
                            candidate.cached_input_credits_micro_per_million_tokens
                        ),
                        cache_write_credits_micro_per_million_tokens=(
                            candidate.cache_write_credits_micro_per_million_tokens
                        ),
                        output_credits_micro_per_million_tokens=(
                            candidate.output_credits_micro_per_million_tokens
                        ),
                        reasoning_output_credits_micro_per_million_tokens=(
                            candidate.reasoning_output_credits_micro_per_million_tokens
                        ),
                        visual_input_credits_micro_per_million_tokens=(
                            candidate.visual_input_credits_micro_per_million_tokens
                        ),
                        image_unit_credits_micro=(
                            candidate.image_unit_credits_micro
                        ),
                        search_call_credits_micro=(
                            candidate.search_call_credits_micro
                        ),
                        link_page_credits_micro=(
                            candidate.link_page_credits_micro
                        ),
                        status="active",
                        created_by=created_by,
                    )
                )
            except IntegrityError as error:
                raise QuotaDomainError(
                    QuotaErrorCode.PRICING_RULE_CONFLICT,
                    "Pricing rule key and version already exist",
                ) from error
            row = connection.execute(
                select(PricingRuleModel).where(PricingRuleModel.id == rule_id)
            ).mappings().one()
        return self._pricing_rule_payload(row)

    def get_pricing_rule(self, pricing_rule_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(PricingRuleModel).where(PricingRuleModel.id == pricing_rule_id)
            ).mappings().first()
        if row is None:
            raise QuotaDomainError(
                QuotaErrorCode.PRICING_RULE_CONFLICT,
                "Pricing rule does not exist",
            )
        return self._pricing_rule_payload(row)

    def list_pricing_rules(self, *, pricing_key: str | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(PricingRuleModel).order_by(
                PricingRuleModel.pricing_key,
                PricingRuleModel.effective_from.desc(),
                PricingRuleModel.version,
            )
            if pricing_key:
                statement = statement.where(PricingRuleModel.pricing_key == pricing_key)
            return [
                self._pricing_rule_payload(row)
                for row in connection.execute(statement).mappings()
            ]

    def retire_pricing_rule(
        self, pricing_rule_id: str, *, actor_user_id: str
    ) -> dict[str, Any]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(PricingRuleModel)
                .where(PricingRuleModel.id == pricing_rule_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise QuotaDomainError(
                    QuotaErrorCode.PRICING_RULE_CONFLICT,
                    "Pricing rule does not exist",
                )
            if row["status"] == "active":
                now = datetime.now(UTC)
                effective_until = row["effective_until"]
                stored_now = _db_time(now)
                if (
                    row["effective_from"] <= stored_now
                    and (effective_until is None or effective_until > stored_now)
                ):
                    effective_until = stored_now
                connection.execute(
                    update(PricingRuleModel)
                    .where(PricingRuleModel.id == pricing_rule_id)
                    .values(status="retired", effective_until=effective_until)
                )
            updated = connection.execute(
                select(PricingRuleModel).where(PricingRuleModel.id == pricing_rule_id)
            ).mappings().one()
        return {**self._pricing_rule_payload(updated), "retired_by": actor_user_id}

    def bind_policy(
        self,
        *,
        subject_type: str,
        subject_id: str,
        policy_id: str,
        priority: int = 0,
        effective_from: datetime,
        effective_until: datetime | None = None,
    ) -> dict[str, Any]:
        if subject_type not in {"default", "role", "user", "workspace", "classroom"}:
            raise ValueError(f"unsupported policy subject_type: {subject_type}")
        if subject_type == "default" and subject_id != "*":
            raise ValueError("default policy bindings must use subject_id='*'")
        start = _utc(effective_from)
        end = _utc(effective_until) if effective_until else None
        if end is not None and end <= start:
            raise ValueError("effective_until must be after effective_from")
        with self._engine.begin() as connection:
            policy = connection.execute(
                select(QuotaPolicyModel).where(
                    QuotaPolicyModel.id == policy_id,
                    QuotaPolicyModel.status == "active",
                )
            ).mappings().first()
            if policy is None:
                raise QuotaDomainError(QuotaErrorCode.POLICY_NOT_FOUND, "Active policy does not exist")
            current = connection.execute(
                select(PolicyBindingModel)
                .where(
                    PolicyBindingModel.subject_type == subject_type,
                    PolicyBindingModel.subject_id == subject_id,
                    PolicyBindingModel.status == "active",
                    PolicyBindingModel.effective_from <= _db_time(start),
                    (PolicyBindingModel.effective_until.is_(None))
                    | (PolicyBindingModel.effective_until > _db_time(start)),
                )
                .with_for_update()
            ).mappings().all()
            for row in current:
                connection.execute(
                    update(PolicyBindingModel)
                    .where(PolicyBindingModel.id == row["id"])
                    .values(effective_until=_db_time(start), updated_at=_db_time(start))
                )
            binding_id = str(uuid.uuid4())
            connection.execute(
                insert(PolicyBindingModel).values(
                    id=binding_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    policy_id=policy_id,
                    priority=priority,
                    status="active",
                    effective_from=_db_time(start),
                    effective_until=_db_time(end) if end else None,
                )
            )
            row = connection.execute(
                select(PolicyBindingModel).where(PolicyBindingModel.id == binding_id)
            ).mappings().one()
        return self._binding_payload(row, policy)

    def list_bindings(self, *, subject_type: str | None = None, subject_id: str | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = (
                select(
                    *PolicyBindingModel.__table__.c,
                    QuotaPolicyModel.code.label("policy_code"),
                    QuotaPolicyModel.version.label("policy_version"),
                )
                .join(QuotaPolicyModel, QuotaPolicyModel.id == PolicyBindingModel.policy_id)
                .order_by(PolicyBindingModel.subject_type, PolicyBindingModel.subject_id, PolicyBindingModel.effective_from.desc())
            )
            if subject_type:
                statement = statement.where(PolicyBindingModel.subject_type == subject_type)
            if subject_id:
                statement = statement.where(PolicyBindingModel.subject_id == subject_id)
            result = []
            for row in connection.execute(statement).mappings():
                result.append(
                    self._binding_payload(
                        row,
                        {"code": row["policy_code"], "version": row["policy_version"]},
                    )
                )
            return result

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    *PolicyBindingModel.__table__.c,
                    QuotaPolicyModel.code.label("policy_code"),
                    QuotaPolicyModel.version.label("policy_version"),
                )
                .join(QuotaPolicyModel, QuotaPolicyModel.id == PolicyBindingModel.policy_id)
                .where(PolicyBindingModel.id == binding_id)
            ).mappings().first()
        if row is None:
            raise QuotaDomainError(QuotaErrorCode.BINDING_CONFLICT, "Policy binding does not exist")
        return self._binding_payload(row, {"code": row["policy_code"], "version": row["policy_version"]})

    def retire_binding(self, binding_id: str, *, actor_user_id: str) -> dict[str, Any]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(PolicyBindingModel)
                .where(PolicyBindingModel.id == binding_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise QuotaDomainError(QuotaErrorCode.BINDING_CONFLICT, "Policy binding does not exist")
            if row["status"] == "active":
                connection.execute(
                    update(PolicyBindingModel)
                    .where(PolicyBindingModel.id == binding_id)
                    .values(status="retired", updated_at=_db_time(datetime.now(UTC)))
                )
                # bind_policy closes the predecessor at the replacement's
                # start.  If the replacement is retired, reopen that exact
                # predecessor so a failed rollout cannot leave a policy gap.
                predecessor = connection.execute(
                    select(PolicyBindingModel)
                    .where(
                        PolicyBindingModel.subject_type == row["subject_type"],
                        PolicyBindingModel.subject_id == row["subject_id"],
                        PolicyBindingModel.status == "active",
                        PolicyBindingModel.effective_until == row["effective_from"],
                    )
                    .order_by(PolicyBindingModel.effective_from.desc())
                    .with_for_update()
                ).mappings().first()
                if predecessor is not None:
                    connection.execute(
                        update(PolicyBindingModel)
                        .where(PolicyBindingModel.id == predecessor["id"])
                        .values(
                            effective_until=row["effective_until"],
                            updated_at=_db_time(datetime.now(UTC)),
                        )
                    )
            updated = connection.execute(
                select(
                    *PolicyBindingModel.__table__.c,
                    QuotaPolicyModel.code.label("policy_code"),
                    QuotaPolicyModel.version.label("policy_version"),
                )
                .join(QuotaPolicyModel, QuotaPolicyModel.id == PolicyBindingModel.policy_id)
                .where(PolicyBindingModel.id == binding_id)
            ).mappings().one()
        return {
            **self._binding_payload(updated, {"code": updated["policy_code"], "version": updated["policy_version"]}),
            "retired_by": actor_user_id,
        }

    def explain_policy(
        self,
        *,
        user_id: str,
        workspace_id: str | None,
        role_codes: Sequence[str],
        classroom_ids: Sequence[str] = (),
        at: datetime,
    ) -> dict[str, Any]:
        at = _utc(at)
        with self._engine.connect() as connection:
            bindings = self._load_bindings(connection, at)
        base_bindings = [item for item in bindings if item.subject_type != "workspace"]
        base = self._resolve_or_raise(
            base_bindings,
            user_id=user_id,
            workspace_id=None,
            role_codes=role_codes,
            classroom_ids=classroom_ids,
            at=at,
        )
        workspace = None
        if workspace_id is not None:
            workspace_bindings = [item for item in bindings if item.subject_type == "workspace"]
            if workspace_bindings:
                workspace = self._resolve_or_none(
                    workspace_bindings,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    role_codes=(),
                    classroom_ids=(),
                    at=at,
                )
        counts = Counter(item.subject_type for item in base_bindings if self._eligible(item, user_id, role_codes, classroom_ids))
        return {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "evaluated_at": at.isoformat(),
            "base": self._explanation_payload(base),
            "workspace": self._explanation_payload(workspace) if workspace else None,
            "candidates": dict(counts),
        }

    def create_grant(
        self,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        allocated_micro: int,
        source_type: str,
        created_by: str,
        reason: str,
        idempotency_key: str,
        effective_from: datetime,
        expires_at: datetime | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        with self._engine.begin() as connection:
            return self.create_grant_in_transaction(
                connection,
                owner_type=owner_type,
                owner_id=owner_id,
                bucket_type=bucket_type,
                period_start=period_start,
                period_end=period_end,
                allocated_micro=allocated_micro,
                source_type=source_type,
                created_by=created_by,
                reason=reason,
                idempotency_key=idempotency_key,
                effective_from=effective_from,
                expires_at=expires_at,
                source_id=source_id,
            )

    def create_grant_in_transaction(
        self,
        connection: Any,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        allocated_micro: int,
        source_type: str,
        created_by: str,
        reason: str,
        idempotency_key: str,
        effective_from: datetime,
        expires_at: datetime | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        start, end = _validate_period(
            owner_type=owner_type,
            bucket_type=bucket_type,
            period_start=period_start,
            period_end=period_end,
        )
        if source_type not in _SOURCE_TYPES:
            raise ValueError(f"unsupported grant source_type: {source_type}")
        if isinstance(allocated_micro, bool) or allocated_micro < 0:
            raise ValueError("allocated_micro must be a non-negative integer")
        effective = _utc(effective_from)
        expires = _utc(expires_at) if expires_at else None
        if expires is not None and expires <= effective:
            raise ValueError("expires_at must be after effective_from")
        existing = connection.execute(
            select(QuotaGrantModel)
            .where(
                QuotaGrantModel.owner_type == owner_type,
                QuotaGrantModel.owner_id == owner_id,
                QuotaGrantModel.idempotency_key == idempotency_key,
            )
            .with_for_update()
        ).mappings().first()
        if existing is not None:
            if not self._grant_request_matches(
                existing,
                owner_type,
                owner_id,
                bucket_type,
                start,
                end,
                allocated_micro,
                source_type,
                effective,
                expires,
            ):
                raise QuotaDomainError(QuotaErrorCode.GRANT_CONFLICT, "Grant idempotency key conflicts with existing allocation")
            return self._grant_payload(existing)
        grant_id = str(uuid.uuid4())
        try:
            with connection.begin_nested():
                connection.execute(
                    insert(QuotaGrantModel).values(
                        id=grant_id,
                        owner_type=owner_type,
                        owner_id=owner_id,
                        bucket_type=bucket_type,
                        period_start=_db_time(start),
                        period_end=_db_time(end),
                        source_type=source_type,
                        source_id=source_id,
                        allocated_micro=allocated_micro,
                        effective_from=_db_time(effective),
                        expires_at=_db_time(expires) if expires else None,
                        status="active",
                        reason=reason,
                        created_by=created_by,
                        idempotency_key=idempotency_key,
                    )
                )
        except IntegrityError:
            winner = connection.execute(
                select(QuotaGrantModel)
                .where(
                    QuotaGrantModel.owner_type == owner_type,
                    QuotaGrantModel.owner_id == owner_id,
                    QuotaGrantModel.idempotency_key == idempotency_key,
                )
                .with_for_update()
            ).mappings().first()
            if winner is None:
                raise
            if not self._grant_request_matches(
                winner,
                owner_type,
                owner_id,
                bucket_type,
                start,
                end,
                allocated_micro,
                source_type,
                effective,
                expires,
            ):
                raise QuotaDomainError(
                    QuotaErrorCode.GRANT_CONFLICT,
                    "Grant idempotency key conflicts with existing allocation",
                )
            return self._grant_payload(winner)
        self._management_ledger(
            connection,
            grant_id=grant_id,
            entry_type="grant",
            amount_micro=allocated_micro,
            # The management idempotency key is scoped by owner.  The
            # ledger key is global, so use the generated grant identity
            # to allow the same request key for different owners.
            idempotency_key=f"grant:{grant_id}",
            actor_user_id=created_by,
            reason=reason,
            metadata={"owner_type": owner_type, "owner_id": owner_id, "bucket_type": bucket_type},
        )
        row = connection.execute(
            select(QuotaGrantModel).where(QuotaGrantModel.id == grant_id)
        ).mappings().one()
        return self._grant_payload(row)

    def get_grant(self, grant_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(QuotaGrantModel).where(QuotaGrantModel.id == grant_id)
            ).mappings().first()
        if row is None:
            raise QuotaDomainError(QuotaErrorCode.GRANT_CONFLICT, "Grant does not exist")
        return self._grant_payload(row)

    def list_grants(self, *, owner_type: str | None = None, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaGrantModel).order_by(QuotaGrantModel.created_at.desc())
            if owner_type:
                statement = statement.where(QuotaGrantModel.owner_type == owner_type)
            if owner_id:
                statement = statement.where(QuotaGrantModel.owner_id == owner_id)
            return [self._grant_payload(row) for row in connection.execute(statement).mappings()]

    def revoke_grant(self, grant_id: str, *, actor_user_id: str, idempotency_key: str) -> dict[str, Any]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(QuotaGrantModel)
                .where(QuotaGrantModel.id == grant_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise QuotaDomainError(QuotaErrorCode.GRANT_CONFLICT, "Grant does not exist")
            if row["status"] != "active":
                if row["revocation_idempotency_key"] != idempotency_key:
                    raise QuotaDomainError(QuotaErrorCode.GRANT_CONFLICT, "Grant is already closed")
                return self._grant_payload(row)
            now = datetime.now(UTC)
            connection.execute(
                update(QuotaGrantModel)
                .where(QuotaGrantModel.id == grant_id)
                .values(
                    status="revoked",
                    revoked_at=_db_time(now),
                    revoked_by=actor_user_id,
                    revocation_idempotency_key=idempotency_key,
                    updated_at=_db_time(now),
                )
            )
            self._management_ledger(
                connection,
                grant_id=grant_id,
                entry_type="grant_revoke",
                amount_micro=-int(row["allocated_micro"]),
                idempotency_key=f"grant-revoke:{idempotency_key}",
                actor_user_id=actor_user_id,
                reason="grant_revoked",
                metadata={"grant_id": grant_id},
            )
            updated = connection.execute(
                select(QuotaGrantModel).where(QuotaGrantModel.id == grant_id)
            ).mappings().one()
        return self._grant_payload(updated)

    def expire_grants(self, *, now: datetime) -> int:
        now = _utc(now)
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(QuotaGrantModel)
                .where(
                    QuotaGrantModel.status == "active",
                    QuotaGrantModel.expires_at.is_not(None),
                    QuotaGrantModel.expires_at <= _db_time(now),
                )
                .with_for_update()
            ).mappings().all()
            for row in rows:
                connection.execute(
                    update(QuotaGrantModel)
                    .where(QuotaGrantModel.id == row["id"])
                    .values(status="expired", updated_at=_db_time(now))
                )
                self._management_ledger(
                    connection,
                    grant_id=row["id"],
                    entry_type="grant_expire",
                    amount_micro=-int(row["allocated_micro"]),
                    idempotency_key=f"grant-expire:{row['id']}",
                    actor_user_id=None,
                    reason="grant_expired",
                    metadata={"grant_id": row["id"]},
                )
            return len(rows)

    def create_adjustment(
        self,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        amount_micro: int,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        start, end = _validate_period(
            owner_type=owner_type,
            bucket_type=bucket_type,
            period_start=period_start,
            period_end=period_end,
        )
        if isinstance(amount_micro, bool) or not isinstance(amount_micro, int):
            raise ValueError("amount_micro must be an integer")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(QuotaAdjustmentModel)
                .where(QuotaAdjustmentModel.idempotency_key == idempotency_key)
                .with_for_update()
            ).mappings().first()
            if existing is not None:
                same = all(
                    (
                        existing["owner_type"] == owner_type,
                        existing["owner_id"] == owner_id,
                        existing["bucket_type"] == bucket_type,
                        existing["period_start"] == _db_time(start),
                        existing["period_end"] == _db_time(end),
                        int(existing["amount_micro"]) == amount_micro,
                        existing["actor_user_id"] == actor_user_id,
                        existing["reason"] == reason,
                    )
                )
                if not same:
                    raise QuotaDomainError(QuotaErrorCode.ADJUSTMENT_CONFLICT, "Adjustment idempotency key conflicts with existing entry")
                return self._adjustment_payload(existing)
            adjustment_id = str(uuid.uuid4())
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(QuotaAdjustmentModel).values(
                            id=adjustment_id,
                            owner_type=owner_type,
                            owner_id=owner_id,
                            bucket_type=bucket_type,
                            period_start=_db_time(start),
                            period_end=_db_time(end),
                            amount_micro=amount_micro,
                            actor_user_id=actor_user_id,
                            reason=reason,
                            idempotency_key=idempotency_key,
                        )
                    )
            except IntegrityError:
                winner = connection.execute(
                    select(QuotaAdjustmentModel)
                    .where(QuotaAdjustmentModel.idempotency_key == idempotency_key)
                    .with_for_update()
                ).mappings().first()
                if winner is None:
                    raise
                same = all(
                    (
                        winner["owner_type"] == owner_type,
                        winner["owner_id"] == owner_id,
                        winner["bucket_type"] == bucket_type,
                        winner["period_start"] == _db_time(start),
                        winner["period_end"] == _db_time(end),
                        int(winner["amount_micro"]) == amount_micro,
                        winner["actor_user_id"] == actor_user_id,
                        winner["reason"] == reason,
                    )
                )
                if not same:
                    raise QuotaDomainError(
                        QuotaErrorCode.ADJUSTMENT_CONFLICT,
                        "Adjustment idempotency key conflicts with existing entry",
                    )
                return self._adjustment_payload(winner)
            self._management_ledger(
                connection,
                grant_id=None,
                entry_type="adjustment",
                amount_micro=amount_micro,
                idempotency_key=f"adjustment:{idempotency_key}",
                actor_user_id=actor_user_id,
                reason=reason,
                metadata={"owner_type": owner_type, "owner_id": owner_id, "bucket_type": bucket_type},
            )
            row = connection.execute(
                select(QuotaAdjustmentModel).where(QuotaAdjustmentModel.id == adjustment_id)
            ).mappings().one()
        return self._adjustment_payload(row)

    def get_adjustment(self, adjustment_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(QuotaAdjustmentModel).where(QuotaAdjustmentModel.id == adjustment_id)
            ).mappings().first()
        if row is None:
            raise QuotaDomainError(
                QuotaErrorCode.ADJUSTMENT_CONFLICT,
                "Adjustment does not exist",
            )
        return self._adjustment_payload(row)

    def list_adjustments(self, *, owner_type: str | None = None, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaAdjustmentModel).order_by(QuotaAdjustmentModel.created_at.desc())
            if owner_type:
                statement = statement.where(QuotaAdjustmentModel.owner_type == owner_type)
            if owner_id:
                statement = statement.where(QuotaAdjustmentModel.owner_id == owner_id)
            return [self._adjustment_payload(row) for row in connection.execute(statement).mappings()]

    @staticmethod
    def _policy_payload(row: Any) -> dict[str, Any]:
        return {
            "policy_id": row["id"],
            "code": row["code"],
            "version": row["version"],
            "name": row["name"],
            "status": row["status"],
            "request_limit_micro": row["request_limit_micro"],
            "daily_limit_micro": row["daily_limit_micro"],
            "weekly_limit_micro": row["weekly_limit_micro"],
            "concurrency_limit": row["concurrency_limit"],
            "max_overdraft_micro": row["max_overdraft_micro"],
            "allowed_model_profiles": list(row["allowed_model_profiles"] or []),
            "unlimited": bool(row["unlimited"]),
            "effective_from": _iso(row["effective_from"]),
            "effective_until": _iso(row["effective_until"]),
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    @staticmethod
    def _pricing_rule_from_row(row: Any) -> PricingRule:
        return PricingRule(
            pricing_key=row["pricing_key"],
            version=row["version"],
            effective_from=(
                row["effective_from"].replace(tzinfo=UTC)
                if row["effective_from"].tzinfo is None
                else row["effective_from"]
            ),
            effective_until=(
                row["effective_until"].replace(tzinfo=UTC)
                if row["effective_until"] is not None
                and row["effective_until"].tzinfo is None
                else row["effective_until"]
            ),
            ordinary_input_credits_micro_per_million_tokens=int(
                row["ordinary_input_credits_micro_per_million_tokens"]
            ),
            cached_input_credits_micro_per_million_tokens=int(
                row["cached_input_credits_micro_per_million_tokens"]
            ),
            cache_write_credits_micro_per_million_tokens=int(
                row["cache_write_credits_micro_per_million_tokens"]
            ),
            output_credits_micro_per_million_tokens=int(
                row["output_credits_micro_per_million_tokens"]
            ),
            reasoning_output_credits_micro_per_million_tokens=(
                int(row["reasoning_output_credits_micro_per_million_tokens"])
                if row["reasoning_output_credits_micro_per_million_tokens"] is not None
                else None
            ),
            visual_input_credits_micro_per_million_tokens=(
                int(row["visual_input_credits_micro_per_million_tokens"])
                if row["visual_input_credits_micro_per_million_tokens"] is not None
                else None
            ),
            image_unit_credits_micro=(
                int(row["image_unit_credits_micro"])
                if row["image_unit_credits_micro"] is not None
                else None
            ),
            search_call_credits_micro=(
                int(row["search_call_credits_micro"])
                if row["search_call_credits_micro"] is not None
                else None
            ),
            link_page_credits_micro=(
                int(row["link_page_credits_micro"])
                if row["link_page_credits_micro"] is not None
                else None
            ),
        )

    @staticmethod
    def _pricing_rule_payload(row: Any) -> dict[str, Any]:
        return {
            "pricing_rule_id": row["id"],
            "pricing_key": row["pricing_key"],
            "version": row["version"],
            "effective_from": _iso(row["effective_from"]),
            "effective_until": _iso(row["effective_until"]),
            "ordinary_input_credits_micro_per_million_tokens": int(
                row["ordinary_input_credits_micro_per_million_tokens"]
            ),
            "cached_input_credits_micro_per_million_tokens": int(
                row["cached_input_credits_micro_per_million_tokens"]
            ),
            "cache_write_credits_micro_per_million_tokens": int(
                row["cache_write_credits_micro_per_million_tokens"]
            ),
            "output_credits_micro_per_million_tokens": int(
                row["output_credits_micro_per_million_tokens"]
            ),
            "reasoning_output_credits_micro_per_million_tokens": (
                int(row["reasoning_output_credits_micro_per_million_tokens"])
                if row["reasoning_output_credits_micro_per_million_tokens"] is not None
                else None
            ),
            "visual_input_credits_micro_per_million_tokens": (
                int(row["visual_input_credits_micro_per_million_tokens"])
                if row["visual_input_credits_micro_per_million_tokens"] is not None
                else None
            ),
            "image_unit_credits_micro": (
                int(row["image_unit_credits_micro"])
                if row["image_unit_credits_micro"] is not None
                else None
            ),
            "search_call_credits_micro": (
                int(row["search_call_credits_micro"])
                if row["search_call_credits_micro"] is not None
                else None
            ),
            "link_page_credits_micro": (
                int(row["link_page_credits_micro"])
                if row["link_page_credits_micro"] is not None
                else None
            ),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _binding_payload(row: Any, policy_row: Any) -> dict[str, Any]:
        return {
            "binding_id": row["id"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "policy_id": row["policy_id"],
            "policy_code": policy_row["code"],
            "policy_version": policy_row["version"],
            "priority": row["priority"],
            "status": row["status"],
            "effective_from": _iso(row["effective_from"]),
            "effective_until": _iso(row["effective_until"]),
        }

    @staticmethod
    def _grant_payload(row: Any) -> dict[str, Any]:
        return {
            "grant_id": row["id"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
            "bucket_type": row["bucket_type"],
            "period_start": _iso(row["period_start"]),
            "period_end": _iso(row["period_end"]),
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "allocated_micro": int(row["allocated_micro"]),
            "effective_from": _iso(row["effective_from"]),
            "expires_at": _iso(row["expires_at"]),
            "status": row["status"],
            "reason": row["reason"],
            "created_by": row["created_by"],
            "idempotency_key": row["idempotency_key"],
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_by": row["revoked_by"],
            "revocation_idempotency_key": row["revocation_idempotency_key"],
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _adjustment_payload(row: Any) -> dict[str, Any]:
        return {
            "adjustment_id": row["id"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
            "bucket_type": row["bucket_type"],
            "period_start": _iso(row["period_start"]),
            "period_end": _iso(row["period_end"]),
            "amount_micro": int(row["amount_micro"]),
            "actor_user_id": row["actor_user_id"],
            "reason": row["reason"],
            "idempotency_key": row["idempotency_key"],
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _explanation_payload(binding: PolicyBinding | None) -> dict[str, Any] | None:
        if binding is None:
            return None
        policy = binding.policy
        return {
            "policy_id": policy.policy_id,
            "code": policy.code,
            "version": policy.version,
            "reason": {
                "subject_type": binding.subject_type,
                "subject_id": binding.subject_id,
                "priority": binding.priority,
            },
            "limits": {
                "request_limit_micro": policy.request_limit_micro,
                "daily_limit_micro": policy.daily_limit_micro,
                "weekly_limit_micro": policy.weekly_limit_micro,
                "concurrency_limit": policy.concurrency_limit,
            },
        }

    @staticmethod
    def _grant_request_matches(
        row: Any,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        start: datetime,
        end: datetime,
        allocated_micro: int,
        source_type: str,
        effective_from: datetime,
        expires_at: datetime | None,
    ) -> bool:
        return all(
            (
                row["owner_type"] == owner_type,
                row["owner_id"] == owner_id,
                row["bucket_type"] == bucket_type,
                row["period_start"] == _db_time(start),
                row["period_end"] == _db_time(end),
                int(row["allocated_micro"]) == allocated_micro,
                row["source_type"] == source_type,
                row["effective_from"] == _db_time(effective_from),
                row["expires_at"] == (_db_time(expires_at) if expires_at else None),
            )
        )

    @staticmethod
    def _management_ledger(connection: Any, *, grant_id: str | None, entry_type: str, amount_micro: int, idempotency_key: str, actor_user_id: str | None, reason: str, metadata: dict[str, Any]) -> None:
        connection.execute(
            insert(QuotaLedgerEntryModel).values(
                id=str(uuid.uuid4()),
                reservation_id=None,
                bucket_id=None,
                grant_id=grant_id,
                entry_type=entry_type,
                amount_micro=amount_micro,
                reserved_delta_micro=0,
                consumed_delta_micro=0,
                idempotency_key=idempotency_key,
                actor_user_id=actor_user_id,
                reason=reason,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _eligible(binding: PolicyBinding, user_id: str, role_codes: Sequence[str], classroom_ids: Sequence[str]) -> bool:
        return (
            (binding.subject_type == "default" and binding.subject_id == "*")
            or (binding.subject_type == "user" and binding.subject_id == user_id)
            or (binding.subject_type == "role" and binding.subject_id in set(role_codes))
            or (binding.subject_type == "classroom" and binding.subject_id in set(classroom_ids))
        )

    @staticmethod
    def _resolve_or_raise(bindings: list[PolicyBinding], **kwargs: Any) -> PolicyBinding:
        try:
            return resolve_effective_policy(bindings, **kwargs)
        except QuotaDomainError as error:
            raise error

    @staticmethod
    def _resolve_or_none(bindings: list[PolicyBinding], **kwargs: Any) -> PolicyBinding | None:
        try:
            return resolve_effective_policy(bindings, **kwargs)
        except QuotaDomainError as error:
            if error.code is QuotaErrorCode.POLICY_NOT_FOUND:
                return None
            raise

    @staticmethod
    def _load_bindings(connection: Any, at: datetime) -> list[PolicyBinding]:
        policy_rows = {
            row["id"]: row for row in connection.execute(select(QuotaPolicyModel)).mappings()
        }
        result: list[PolicyBinding] = []
        for row in connection.execute(select(PolicyBindingModel)).mappings():
            policy = policy_rows.get(row["policy_id"])
            if policy is None or policy["status"] != "active" or row["status"] != "active":
                continue
            binding_start = row["effective_from"]
            policy_start = policy["effective_from"]
            binding_start = binding_start.replace(tzinfo=UTC) if binding_start.tzinfo is None else binding_start
            policy_start = policy_start.replace(tzinfo=UTC) if policy_start.tzinfo is None else policy_start
            binding_end = row["effective_until"]
            policy_end = policy["effective_until"]
            binding_end = binding_end.replace(tzinfo=UTC) if binding_end is not None and binding_end.tzinfo is None else binding_end
            policy_end = policy_end.replace(tzinfo=UTC) if policy_end is not None and policy_end.tzinfo is None else policy_end
            if not (binding_start <= at and (binding_end is None or at < binding_end)):
                continue
            if not (policy_start <= at and (policy_end is None or at < policy_end)):
                continue
            result.append(
                PolicyBinding(
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    policy=QuotaPolicy(
                        policy_id=policy["id"],
                        code=policy["code"],
                        version=policy["version"],
                        request_limit_micro=policy["request_limit_micro"],
                        daily_limit_micro=policy["daily_limit_micro"],
                        weekly_limit_micro=policy["weekly_limit_micro"],
                        concurrency_limit=policy["concurrency_limit"],
                        max_overdraft_micro=policy["max_overdraft_micro"],
                        allowed_model_profiles=tuple(policy["allowed_model_profiles"] or ()),
                        unlimited=bool(policy["unlimited"]),
                    ),
                    priority=row["priority"],
                    effective_from=binding_start,
                    effective_until=binding_end,
                )
            )
        return result
