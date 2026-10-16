"""add persistent ordering timestamps to conversation transcripts"""

from alembic import context, op
from sqlalchemy import text


revision = "20260929_46_transcript_time"
down_revision = "20260927_45_merge_storage_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statement = """
        ALTER TABLE `nlp_conversation_transcripts`
        ADD COLUMN `created_at`
            DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6)
    """
    if context.is_offline_mode():
        op.execute(statement)
        return

    connection = op.get_bind()
    column_exists = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'nlp_conversation_transcripts'
              AND column_name = 'created_at'
            """
        )
    ).scalar_one()
    if not column_exists:
        op.execute(statement)


def downgrade() -> None:
    statement = """
        ALTER TABLE `nlp_conversation_transcripts`
        DROP COLUMN `created_at`
    """
    if context.is_offline_mode():
        op.execute(statement)
        return

    connection = op.get_bind()
    column_exists = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'nlp_conversation_transcripts'
              AND column_name = 'created_at'
            """
        )
    ).scalar_one()
    if column_exists:
        op.execute(statement)
