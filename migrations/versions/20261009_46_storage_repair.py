"""Post-history storage repair marker.

This uniquely named revision reconnects the current develop head to the new
file-transfer feature without rewriting any deployed revision.
"""


revision = "20261009_46_storage_repair"
down_revision = "20261001_48_account_delete_self"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
