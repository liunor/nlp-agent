"""add persistent ordering timestamps to conversation transcripts"""

from alembic import op


revision = "20260929_46_transcript_time"
down_revision = "20260927_45_merge_storage_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The foundation migration creates this table from SQLAlchemy metadata for
    # fresh databases.  IF NOT EXISTS keeps this follow-up safe for both fresh
    # installs and databases that already applied the foundation migration.
    op.execute(
        """
        ALTER TABLE `nlp_conversation_transcripts`
        ADD COLUMN IF NOT EXISTS `created_at`
            DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE `nlp_conversation_transcripts`
        DROP COLUMN IF EXISTS `created_at`
        """
    )
