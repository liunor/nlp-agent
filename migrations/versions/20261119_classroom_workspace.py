"""Allow multiple classrooms per workspace while retaining the FK index."""
from alembic import context, op
import sqlalchemy as sa

revision = "20261119_classroom_workspace"
down_revision = "20261118_add_file_distributions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    offline = context.is_offline_mode()
    indexes = {} if offline else {item["name"]: item for item in sa.inspect(op.get_bind()).get_indexes("nlp_classrooms")}
    if offline or "ix_nlp_classrooms_workspace_id" not in indexes:
        op.create_index("ix_nlp_classrooms_workspace_id", "nlp_classrooms", ["workspace_id"])
    if offline or "uq_nlp_classrooms_workspace_id" in indexes:
        op.drop_index("uq_nlp_classrooms_workspace_id", table_name="nlp_classrooms")


def downgrade() -> None:
    # Refuse incompatible existing data rather than deleting or merging classes.
    op.create_unique_constraint("uq_nlp_classrooms_workspace_id", "nlp_classrooms", ["workspace_id"])
    op.drop_index("ix_nlp_classrooms_workspace_id", table_name="nlp_classrooms")
