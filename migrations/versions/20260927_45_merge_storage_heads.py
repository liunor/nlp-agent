"""merge the develop and account-storage migration heads"""

revision = "20260927_45_merge_storage_heads"
down_revision = (
    "20260920_58_knowledge_book_files",
    "20260927_44_storage_pool_lock",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
