"""File upload idempotency and generated-file publication provenance."""
from alembic import op
import sqlalchemy as sa

revision = '20261125_file_workflow'
down_revision = '20261119_classroom_workspace'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nlp_user_files', sa.Column('upload_key', sa.String(128), nullable=True))
    op.add_column('nlp_user_files', sa.Column('origin', sa.String(32), nullable=False, server_default='user'))
    op.create_unique_constraint('uq_nlp_user_files_upload_key', 'nlp_user_files',
                               ['owner_user_id', 'workspace_id', 'upload_key'])


def downgrade():
    op.drop_constraint('uq_nlp_user_files_upload_key', 'nlp_user_files', type_='unique')
    op.drop_column('nlp_user_files', 'origin')
    op.drop_column('nlp_user_files', 'upload_key')
