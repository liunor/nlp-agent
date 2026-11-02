"""add workspace_id to sandbox_environments

Revision ID: 20261102_add_workspace_id
Revises: 填写你当前的最新版本号 (去 versions 目录找最新的 down_revision)
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20261102_add_workspace_id'
down_revision = '20261017_55_t07_sms_history'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('nlp_sandbox_environments', sa.Column('workspace_id', sa.String(length=36), nullable=True))
    
    op.drop_constraint('uq_nlp_sandbox_environments_owner', 'nlp_sandbox_environments', type_='unique')
    
    op.create_unique_constraint('uq_nlp_sandbox_environments_owner_workspace', 'nlp_sandbox_environments', ['owner_user_id', 'workspace_id'])
    
    op.create_foreign_key(
        'fk_nlp_sandbox_environments_workspace', 
        'nlp_sandbox_environments', 'nlp_workspaces',
        ['workspace_id'], ['id'], ondelete='RESTRICT'
    )

def downgrade() -> None:
    op.drop_constraint('fk_nlp_sandbox_environments_workspace', 'nlp_sandbox_environments', type_='foreignkey')
    op.drop_constraint('uq_nlp_sandbox_environments_owner_workspace', 'nlp_sandbox_environments', type_='unique')
    op.create_unique_constraint('uq_nlp_sandbox_environments_owner', 'nlp_sandbox_environments', ['owner_user_id'])
    op.drop_column('nlp_sandbox_environments', 'workspace_id')