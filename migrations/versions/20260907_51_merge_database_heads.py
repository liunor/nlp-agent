"""Merge the phone repair and observability index migration heads."""


revision = "20260907_51_merge_database_heads"
down_revision = (
    "20260907_50_merge_database_heads",
    "20260907_50_obs_indexes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
