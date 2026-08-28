"""add phone registration columns to nlp_users.

The phone-number registration feature ships ``phone_number`` and
``registration_source`` on :class:`~server.infrastructure.mysql.models.UserModel`.
Environments upgraded through the older local chain may already carry these
columns, so the migration is idempotent: it inspects the live table and only
adds what is missing.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260828_33_user_phone"
down_revision = "20260827_32_book_merge"
branch_labels = None
depends_on = None


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("nlp_users")}


def upgrade() -> None:
    columns = _existing_columns()
    if "phone_number" not in columns:
        op.add_column(
            "nlp_users",
            sa.Column("phone_number", sa.String(20), nullable=True),
        )
        op.create_index("ix_nlp_users_phone_number", "nlp_users", ["phone_number"])
        op.create_unique_constraint("uq_nlp_users_phone_number", "nlp_users", ["phone_number"])
    if "registration_source" not in columns:
        op.add_column(
            "nlp_users",
            sa.Column(
                "registration_source",
                sa.String(32),
                nullable=False,
                server_default="manual",
            ),
        )


def downgrade() -> None:
    columns = _existing_columns()
    if "registration_source" in columns:
        op.drop_column("nlp_users", "registration_source")
    if "phone_number" in columns:
        op.drop_constraint("uq_nlp_users_phone_number", "nlp_users", type_="unique")
        op.drop_index("ix_nlp_users_phone_number", table_name="nlp_users")
        op.drop_column("nlp_users", "phone_number")
