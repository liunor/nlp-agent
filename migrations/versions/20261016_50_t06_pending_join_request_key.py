"""allow re-application while keeping one concurrent pending request"""

from alembic import op
import sqlalchemy as sa


revision = "20261016_50_t06"
down_revision = "20261015_49_t06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nlp_class_join_requests",
        sa.Column("pending_key", sa.String(73, collation="ascii_bin"), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE nlp_class_join_requests "
            "SET pending_key = CONCAT(class_id, ':', user_id) "
            "WHERE status = 'pending'"
        )
    )
    op.drop_constraint(
        "uq_nlp_class_join_requests_cls_usr_sts",
        "nlp_class_join_requests",
        type_="unique",
    )
    op.create_index(
        "uq_nlp_class_join_requests_pending_key",
        "nlp_class_join_requests",
        ["pending_key"],
        unique=True,
    )


def downgrade() -> None:
    # A downgrade is only safe when historical rows do not contain repeated
    # approved/rejected states for the same class/user pair.
    op.drop_index(
        "uq_nlp_class_join_requests_pending_key",
        table_name="nlp_class_join_requests",
    )
    op.create_unique_constraint(
        "uq_nlp_class_join_requests_cls_usr_sts",
        "nlp_class_join_requests",
        ["class_id", "user_id", "status"],
    )
    op.drop_column("nlp_class_join_requests", "pending_key")
