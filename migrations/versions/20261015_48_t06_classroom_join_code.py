"""add short join codes to classrooms for student self-service enrollment

Revision ID: 20261015_48_t06
Revises: 20261010_48_cleanup_index
"""

from __future__ import annotations

import secrets

from alembic import context, op
import sqlalchemy as sa


revision = "20261015_48_t06"
down_revision = "20261010_48_cleanup_index"
branch_labels = None
depends_on = None

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code(used: set[str]) -> str:
    for _ in range(5):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if code not in used:
            return code
    raise RuntimeError("unable to allocate a unique classroom join code")


def upgrade() -> None:
    op.add_column(
        "nlp_classrooms",
        sa.Column("join_code", sa.String(length=12), nullable=True),
    )
    if context.is_offline_mode():
        # Offline SQL generation cannot read row IDs for Python-side backfill.
        # MySQL evaluates UUID() per row; the 12-hex codes fit this migration's
        # column and are protected by the unique constraint below.
        op.execute(
            sa.text(
                "UPDATE nlp_classrooms "
                "SET join_code = UPPER(SUBSTRING(REPLACE(UUID(), '-', ''), 1, 12))"
            )
        )
    else:
        bind = op.get_bind()
        rows = list(bind.execute(sa.text("SELECT id FROM nlp_classrooms")).mappings())
        used: set[str] = set()
        for row in rows:
            code = _new_code(used)
            used.add(code)
            bind.execute(
                sa.text("UPDATE nlp_classrooms SET join_code = :join_code WHERE id = :id"),
                {"join_code": code, "id": row["id"]},
            )
    op.alter_column(
        "nlp_classrooms",
        "join_code",
        existing_type=sa.String(length=12),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_nlp_classrooms_join_code", "nlp_classrooms", ["join_code"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_nlp_classrooms_join_code", "nlp_classrooms", type_="unique"
    )
    op.drop_column("nlp_classrooms", "join_code")
