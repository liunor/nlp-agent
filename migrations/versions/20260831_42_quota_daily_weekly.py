"""Rename quota monthly periods to weekly periods.

Quota periods are intentionally limited to daily and weekly windows.  Existing
policy amounts are carried forward to the weekly field; historical buckets,
grants, adjustments, and credit operations remain append-only records, with
their bucket type renamed so old rows continue to be queryable under the new
domain vocabulary.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT


revision = "20260831_42_quota_daily_weekly"
down_revision = "20260831_41_quota_self_usage"
branch_labels = None
depends_on = None


_BUCKET_TABLES = (
    "nlp_quota_buckets",
    "nlp_quota_grants",
    "nlp_quota_adjustments",
    "nlp_quota_credit_operations",
)
_CHECK_NAMES = {
    table_name: f"ck_{table_name}_bucket_type"
    for table_name in _BUCKET_TABLES
}


def _check_constraints(bind, table_name: str) -> list[dict]:
    return sa.inspect(bind).get_check_constraints(table_name)


def _is_bucket_type_constraint(constraint: dict, period: str) -> bool:
    sqltext = (constraint.get("sqltext") or "").lower().replace(" ", "")
    return "bucket_typein" in sqltext and f"'{period}'" in sqltext


def _drop_bucket_type_constraints(bind, table_name: str) -> None:
    constraints = [
        constraint
        for constraint in _check_constraints(bind, table_name)
        if constraint.get("name")
        and "bucket_type" in (constraint.get("sqltext") or "").lower()
    ]
    if bind.dialect.name == "mysql":
        for constraint in constraints:
            op.execute(
                sa.text(
                    f"ALTER TABLE {table_name} DROP CHECK "
                    f"`{constraint['name']}`"
                )
            )
    elif constraints:
        with op.batch_alter_table(table_name) as batch_op:
            for constraint in constraints:
                batch_op.drop_constraint(constraint["name"], type_="check")


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("nlp_quota_policies")
    }
    if "monthly_limit_micro" in columns and "weekly_limit_micro" not in columns:
        op.alter_column(
            "nlp_quota_policies",
            "monthly_limit_micro",
            new_column_name="weekly_limit_micro",
            existing_type=BIGINT(unsigned=True),
            existing_nullable=True,
        )
    for table_name in _BUCKET_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table_name} "
                "SET bucket_type = 'weekly' "
                "WHERE bucket_type = 'monthly'"
            )
        )
        constraint_name = _CHECK_NAMES[table_name]
        bind = op.get_bind()
        existing_constraints = _check_constraints(bind, table_name)
        if not any(
            _is_bucket_type_constraint(constraint, "weekly")
            and _is_bucket_type_constraint(constraint, "daily")
            for constraint in existing_constraints
        ):
            _drop_bucket_type_constraints(bind, table_name)
            if bind.dialect.name == "mysql":
                op.execute(
                    sa.text(
                        f"ALTER TABLE {table_name} ADD CONSTRAINT "
                        f"`{constraint_name}` CHECK "
                        "(bucket_type IN ('daily', 'weekly'))"
                    )
                )
            else:
                with op.batch_alter_table(table_name) as batch_op:
                    batch_op.create_check_constraint(
                        constraint_name,
                        "bucket_type IN ('daily', 'weekly')",
                    )


def downgrade() -> None:
    for table_name in _BUCKET_TABLES:
        constraint_name = _CHECK_NAMES[table_name]
        bind = op.get_bind()
        existing_constraints = _check_constraints(bind, table_name)
        weekly_constraints = [
            constraint
            for constraint in existing_constraints
            if _is_bucket_type_constraint(constraint, "weekly")
        ]
        if weekly_constraints:
            _drop_bucket_type_constraints(bind, table_name)
        op.execute(
            sa.text(
                f"UPDATE {table_name} "
                "SET bucket_type = 'monthly' "
                "WHERE bucket_type = 'weekly'"
            )
        )
        if bind.dialect.name == "mysql":
            op.execute(
                sa.text(
                    f"ALTER TABLE {table_name} ADD CONSTRAINT "
                    f"`{constraint_name}` CHECK "
                    "(bucket_type IN ('daily', 'monthly'))"
                )
            )
        else:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.create_check_constraint(
                    constraint_name,
                    "bucket_type IN ('daily', 'monthly')",
                )
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("nlp_quota_policies")
    }
    if "weekly_limit_micro" in columns and "monthly_limit_micro" not in columns:
        op.alter_column(
            "nlp_quota_policies",
            "weekly_limit_micro",
            new_column_name="monthly_limit_micro",
            existing_type=BIGINT(unsigned=True),
            existing_nullable=True,
        )
