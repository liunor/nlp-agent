"""Give legacy accounts an explicit fixed RBAC role.

The runtime now intentionally evaluates only the four fixed roles. Accounts
created before that policy, or accounts carrying only a custom role, must not
become authenticatable-but-unauthorized after deployment. They receive the
least-privilege ``guest`` role; existing custom assignments remain intact for
rollback/audit purposes but are no longer part of the live authorization
projection.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260903_46_fixed_role_backfill"
down_revision = "20260902_45_merge_auth_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO nlp_user_roles (user_id, role_id, assigned_by_user_id) "
        "SELECT users.id, guest.id, NULL "
        "FROM nlp_users AS users "
        "JOIN nlp_roles AS guest ON guest.code = 'guest' "
        "AND guest.status = 'active' AND guest.is_builtin = 1 "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM nlp_user_roles AS existing "
        "  JOIN nlp_roles AS fixed ON fixed.id = existing.role_id "
        "  WHERE existing.user_id = users.id "
        "  AND fixed.code IN ('guest', 'student', 'teacher', 'developer') "
        "  AND fixed.status = 'active' "
        "  AND (existing.expires_at IS NULL OR existing.expires_at > UTC_TIMESTAMP(6))"
        ")"
    ))


def downgrade() -> None:
    # The migration cannot distinguish these safe backfills from a legitimate
    # guest assignment. Keeping them is safer than removing a user's only
    # authorization identity during downgrade.
    pass
