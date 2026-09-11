"""Repair phone identity schema drift in databases marked past migration 41."""

import re

from alembic import op
import sqlalchemy as sa


revision = "20260905_49_phone_schema_repair"
down_revision = "20260904_48_developer_merge"
branch_labels = None
depends_on = None


def _legacy_normalize(value: str | None) -> str | None:
    raw = str(value or "").strip()
    compact = re.sub(r"[\s().\-\.]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        digits = compact[1:]
    else:
        digits = compact
        if len(digits) == 11 and digits.startswith("1"):
            digits = "86" + digits
    if digits.isdigit() and 7 <= len(digits) <= 15:
        return "+" + digits
    return None


def upgrade() -> None:
    """Repair the column and its data without assuming migration 41 ran fully."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("nlp_users")}
    if "phone_number_normalized" not in columns:
        op.add_column(
            "nlp_users",
            sa.Column("phone_number_normalized", sa.String(16), nullable=True),
        )

    rows = bind.execute(
        sa.text(
            "SELECT id, phone_number FROM nlp_users "
            "WHERE phone_number IS NOT NULL AND phone_number_normalized IS NULL"
        )
    ).fetchall()
    for user_id, phone in rows:
        normalized = _legacy_normalize(phone)
        if normalized:
            bind.execute(
                sa.text(
                    "UPDATE nlp_users SET phone_number_normalized=:phone "
                    "WHERE id=:id"
                ),
                {"phone": normalized, "id": user_id},
            )

    duplicates = bind.execute(
        sa.text(
            "SELECT phone_number_normalized FROM nlp_users "
            "WHERE phone_number_normalized IS NOT NULL "
            "GROUP BY phone_number_normalized HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for (phone,) in duplicates:
        ids = bind.execute(
            sa.text(
                "SELECT id FROM nlp_users WHERE phone_number_normalized=:phone "
                "ORDER BY created_at, id"
            ),
            {"phone": phone},
        ).fetchall()
        for (user_id,) in ids[1:]:
            bind.execute(
                sa.text(
                    "UPDATE nlp_users SET phone_number_normalized=NULL "
                    "WHERE id=:id"
                ),
                {"id": user_id},
            )

    inspector = sa.inspect(bind)
    unique_constraints = inspector.get_unique_constraints("nlp_users")
    unique_indexes = inspector.get_indexes("nlp_users")
    has_phone_unique = any(
        item.get("name") == "uq_nlp_users_phone_number_normalized"
        or (
            item.get("unique")
            and item.get("column_names") == ["phone_number_normalized"]
        )
        for item in [*unique_constraints, *unique_indexes]
    )
    if not has_phone_unique:
        op.create_unique_constraint(
            "uq_nlp_users_phone_number_normalized",
            "nlp_users",
            ["phone_number_normalized"],
        )

    index_names = {item.get("name") for item in unique_indexes}
    if "ix_nlp_users_phone_number_normalized" not in index_names:
        op.create_index(
            "ix_nlp_users_phone_number_normalized",
            "nlp_users",
            ["phone_number_normalized"],
        )


def downgrade() -> None:
    # This migration repairs schema created by an earlier migration. Removing
    # the repaired column here would make downgrade destructive and unsafe.
    pass
