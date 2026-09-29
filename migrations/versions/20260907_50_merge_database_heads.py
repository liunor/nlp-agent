"""Merge the billable-feature and phone-schema migration heads."""


revision = "20260907_50_merge_database_heads"
down_revision = (
    "20260904_49_billable_features",
    "20260905_49_phone_schema_repair",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
