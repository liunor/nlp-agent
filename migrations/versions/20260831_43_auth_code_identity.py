"""Enforce one active verification code per kind and subject."""

from alembic import op
import sqlalchemy as sa

revision = "20260831_43_auth_code_identity"
down_revision = "20260831_42_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Keep the newest row when a pre-constraint deployment contains races.
    bind.execute(sa.text(
        "DELETE older FROM nlp_auth_codes older "
        "JOIN nlp_auth_codes newer ON newer.kind = older.kind "
        "AND newer.subject = older.subject AND "
        "(newer.created_at > older.created_at OR "
        "(newer.created_at = older.created_at AND newer.id > older.id))"
    ))
    names = {item.get("name") for item in sa.inspect(bind).get_unique_constraints("nlp_auth_codes")}
    if "uq_nlp_auth_codes_kind_subject" not in names:
        op.create_unique_constraint(
            "uq_nlp_auth_codes_kind_subject", "nlp_auth_codes", ["kind", "subject"]
        )


def downgrade() -> None:
    op.drop_constraint("uq_nlp_auth_codes_kind_subject", "nlp_auth_codes", type_="unique")
