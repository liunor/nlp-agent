"""add file distributions table for teacher broadcasting

Revision ID: 20261118_add_file_distributions
Revises: 20261102_add_workspace_id
Create Date: 2026-11-18
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

revision = '20261118_add_file_distributions'
down_revision = '20261117_release_note_reads'
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.String(36, collation='ascii_bin')
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    existing_tables = set() if offline else set(inspector.get_table_names())

    if offline or 'nlp_file_distributions' not in existing_tables:
        op.create_table(
            'nlp_file_distributions',
            sa.Column('id', uuid_type, primary_key=True),
            sa.Column('teacher_user_id', uuid_type, nullable=False),
            sa.Column('source_file_id', uuid_type, nullable=True),
            sa.Column('file_name', sa.String(255), nullable=False),
            sa.Column('size_bytes', BIGINT(unsigned=True), nullable=False),
            sa.Column('target_type', sa.String(32), nullable=False),
            sa.Column('target_value', sa.String(128), nullable=True),
            sa.Column('target_label', sa.String(255), nullable=False),
            sa.Column('direct_delivery', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('total_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('status', sa.String(16), nullable=False, server_default='completed'),
            sa.Column('details_json', sa.Text(), nullable=True),
            sa.Column('created_at', DATETIME(fsp=6), nullable=False, server_default=sa.text('UTC_TIMESTAMP(6)')),
            sa.Column('updated_at', DATETIME(fsp=6), nullable=False, server_default=sa.text('UTC_TIMESTAMP(6)')),
            sa.ForeignKeyConstraint(['teacher_user_id'], ['nlp_users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['source_file_id'], ['nlp_user_files.id'], ondelete='SET NULL'),
            comment='教师文件分发批次记录',
        )
        op.create_index(
            'ix_nlp_file_distributions_teacher_created',
            'nlp_file_distributions',
            ['teacher_user_id', 'created_at'],
        )


def downgrade() -> None:
    op.drop_table('nlp_file_distributions')
