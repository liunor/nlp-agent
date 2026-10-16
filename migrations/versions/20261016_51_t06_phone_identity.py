"""restore canonical phone identity for public T06 registration"""

from alembic import op
import sqlalchemy as sa


revision = "20261016_51_t06_phone_identity"
down_revision = "20261016_50_t06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nlp_users", sa.Column("phone_number", sa.String(20), nullable=True))
    op.add_column(
        "nlp_users",
        sa.Column("phone_number_normalized", sa.String(16), nullable=True),
    )
    op.create_index("ix_nlp_users_phone_number", "nlp_users", ["phone_number"])
    op.create_unique_constraint(
        "uq_nlp_users_phone_number_normalized",
        "nlp_users",
        ["phone_number_normalized"],
    )
    op.create_index(
        "ix_nlp_users_phone_number_normalized",
        "nlp_users",
        ["phone_number_normalized"],
    )


def downgrade() -> None:
    op.drop_index("ix_nlp_users_phone_number_normalized", table_name="nlp_users")
    op.drop_constraint(
        "uq_nlp_users_phone_number_normalized", "nlp_users", type_="unique"
    )
    op.drop_index("ix_nlp_users_phone_number", table_name="nlp_users")
    op.drop_column("nlp_users", "phone_number_normalized")
    op.drop_column("nlp_users", "phone_number")
