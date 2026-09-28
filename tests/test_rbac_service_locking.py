from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.rbac import service as rbac_module
from server.rbac.service import RbacService
from server.web.contracts import ReplaceUserRolesBody


class _Result:
    def __init__(self, values=()):
        self._values = list(values)

    def all(self):
        return list(self._values)

    def scalars(self):
        return iter(self._values)


class _RecordingSession:
    def __init__(self):
        self.scalar_statements = []
        self.execute_statements = []
        self.user = SimpleNamespace(id="target-user", authorization_version=1)
        self.developer_role = SimpleNamespace(id="developer-role", code="developer")
        self.student_role = SimpleNamespace(id="student-role", code="student")
        self.guest_role = SimpleNamespace(id="guest-role", code="guest")
        self.requested_empty = False

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        if len(self.scalar_statements) == 1:
            return self.developer_role
        return self.user

    async def scalars(self, _statement):
        return _Result([self.guest_role if self.requested_empty else self.student_role])

    async def execute(self, statement):
        self.execute_statements.append(statement)
        return _Result(["guest" if self.requested_empty else "student"])

    def add_all(self, _values):
        return None

    def add(self, _value):
        return None

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_role_replacement_locks_developer_role_before_target_user(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(
        rbac_module.sandbox_lifecycle_service,
        "revoke_user_leases",
        AsyncMock(),
    )
    monkeypatch.setattr(rbac_module.rbac_service, "audit", AsyncMock())

    assigned = await RbacService().replace_user_roles(
        session,
        user_id="target-user",
        role_codes={"student"},
        assigned_by_user_id="admin-user",
    )

    assert assigned == {"student"}
    assert len(session.scalar_statements) >= 2
    assert "nlp_roles" in str(session.scalar_statements[0]).lower()
    assert session.scalar_statements[0]._for_update_arg is not None
    assert "nlp_users" in str(session.scalar_statements[1]).lower()
    assert session.scalar_statements[1]._for_update_arg is not None


@pytest.mark.asyncio
async def test_empty_role_replacement_keeps_the_guest_safety_net(monkeypatch):
    session = _RecordingSession()
    session.requested_empty = True
    monkeypatch.setattr(
        rbac_module.sandbox_lifecycle_service,
        "revoke_user_leases",
        AsyncMock(),
    )
    monkeypatch.setattr(rbac_module.rbac_service, "audit", AsyncMock())

    assigned = await RbacService().replace_user_roles(
        session,
        user_id="target-user",
        role_codes=set(),
        assigned_by_user_id="admin-user",
    )

    assert assigned == {"guest"}


def test_role_update_can_clear_roles_to_the_guest_default():
    assert ReplaceUserRolesBody(role_codes=[]).role_codes == set()
