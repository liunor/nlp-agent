"""add workspace_id to sandbox_environments

Revision ID: 20261102_add_workspace_id
Revises: 20261017_55_t07_sms_history
Create Date: 2026-11-02
"""
from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20261102_add_workspace_id'
down_revision = '20261017_55_t07_sms_history'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # MySQL DDL commits immediately. The previous revision may already have
    # added the column before failing to drop the FK-supporting owner index.
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    table = 'nlp_sandbox_environments'
    if offline or 'workspace_id' not in {c['name'] for c in inspector.get_columns(table)}:
        op.add_column(table, sa.Column('workspace_id', sa.String(36, collation='ascii_bin'), nullable=True))
    else:
        op.alter_column(table, 'workspace_id', existing_type=sa.String(36),
                        type_=sa.String(36, collation='ascii_bin'), existing_nullable=True)
    unique_names = set() if offline else {c['name'] for c in inspector.get_unique_constraints(table)}
    # Its leftmost owner column must replace the child FK's supporting index
    # BEFORE the old index is removed.
    if offline or 'uq_nlp_sandbox_environments_owner_workspace' not in unique_names:
        op.create_unique_constraint('uq_nlp_sandbox_environments_owner_workspace', table, ['owner_user_id', 'workspace_id'])
    if offline or 'uq_nlp_sandbox_environments_owner' in unique_names:
        op.drop_constraint('uq_nlp_sandbox_environments_owner', table, type_='unique')
    foreign_keys = set() if offline else {c['name'] for c in inspector.get_foreign_keys(table)}
    if offline or 'fk_nlp_sandbox_environments_workspace' not in foreign_keys:
        op.create_foreign_key('fk_nlp_sandbox_environments_workspace', table, 'nlp_workspaces',
                              ['workspace_id'], ['id'], ondelete='RESTRICT')

def downgrade() -> None:
    # Refuse to discard workspace-specific environments: this unique index
    # fails explicitly if an owner now has more than one environment.
    op.create_unique_constraint('uq_nlp_sandbox_environments_owner', 'nlp_sandbox_environments', ['owner_user_id'])
    op.drop_constraint('fk_nlp_sandbox_environments_workspace', 'nlp_sandbox_environments', type_='foreignkey')
    op.drop_constraint('uq_nlp_sandbox_environments_owner_workspace', 'nlp_sandbox_environments', type_='unique')
    op.drop_column('nlp_sandbox_environments', 'workspace_id')
