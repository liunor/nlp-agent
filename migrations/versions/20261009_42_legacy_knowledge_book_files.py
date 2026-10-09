"""Compatibility anchor for the historical knowledge-book-files revision.

The current branch does not recreate the unrelated historical knowledge-book
chain.  Keeping this revision resolvable is enough for databases that already
applied it to reach the storage merge safely.
"""


revision = "20260920_58_knowledge_book_files"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
