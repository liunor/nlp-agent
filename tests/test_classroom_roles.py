import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from core.identity import AccessDeniedError
from server.classroom_join.service import list_active_classroom_members
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import ClassroomMemberModel, UserModel, UserRoleModel, WorkspaceMemberModel
from server.rbac.service import rbac_service
from server.user.schemas import UserCreate
from server.user.service import UserService


@pytest.fixture
async def classroom_roles(monkeypatch):
    url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not url:
        pytest.skip("MySQL integration database is not configured")
    monkeypatch.setattr("core.rbac.record_authorization_decision", lambda *args, **kwargs: None)
    engine = create_engine(DatabaseConfig(url))
    try:
        async with create_session_factory(engine)() as db:
            transaction = await db.begin()
            try:
                users = {}
                for key, role in [("owner", "teacher"), ("learner", "teacher"), ("developer", "developer"), ("student", "student"), ("guest", "guest")]:
                    users[key] = await UserService(db).create_user(UserCreate(username="review" + uuid4().hex, display_name=key, password="Classroom-test-123!"), initial_role_code=role)
                workspace = await db.scalar(select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == users["owner"].id))
                classroom = await rbac_service.create_classroom(db, workspace_id=workspace, name="Role regression", actor_user_id=users["owner"].id)
                await db.flush()
                for key in ("learner", "student", "guest"):
                    db.add(ClassroomMemberModel(classroom_id=classroom.id, user_id=users[key].id, member_role="student", status="active"))
                await db.flush()
                yield db, classroom, users
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("key, allowed", [("owner", True), ("learner", False), ("developer", True), ("student", False), ("guest", False)])
async def test_classroom_management_uses_teaching_role_not_any_membership(classroom_roles, key, allowed):
    db, classroom, users = classroom_roles
    principal = await rbac_service.principal_for_user_id(db, users[key].id)
    if allowed:
        result = await rbac_service.require_classroom_manager(db, principal, classroom.id)
        assert result.id == classroom.id
    else:
        with pytest.raises(AccessDeniedError):
            await rbac_service.require_classroom_manager(db, principal, classroom.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "disabled", "expired", "role_removed"])
async def test_roster_contains_only_effective_students(classroom_roles, state):
    db, classroom, users = classroom_roles
    if state == "disabled":
        await db.execute(update(UserModel).where(UserModel.id == users["student"].id).values(status="disabled"))
    elif state == "expired":
        await db.execute(update(UserRoleModel).where(UserRoleModel.user_id == users["student"].id).values(expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)))
    elif state == "role_removed":
        from sqlalchemy import delete
        await db.execute(delete(UserRoleModel).where(UserRoleModel.user_id == users["student"].id))
    members = await list_active_classroom_members(db, classroom.id)
    assert {member.user_id for member in members} == ({users["student"].id} if state == "active" else set())


@pytest.mark.asyncio
async def test_revoked_teaching_assignment_is_checked_again(classroom_roles):
    db, classroom, users = classroom_roles
    principal = await rbac_service.principal_for_user_id(db, users["owner"].id)
    await db.execute(update(ClassroomMemberModel).where(ClassroomMemberModel.classroom_id == classroom.id, ClassroomMemberModel.user_id == principal.user_id).values(status="disabled"))
    with pytest.raises(AccessDeniedError):
        await rbac_service.require_classroom_manager(db, principal, classroom.id)


@pytest.mark.asyncio
async def test_developer_still_needs_management_capability(classroom_roles):
    db, classroom, users = classroom_roles
    principal = await rbac_service.principal_for_user_id(db, users["developer"].id)
    principal = principal.model_copy(update={"permissions": frozenset({"quota:usage:read_self"})})
    with pytest.raises(AccessDeniedError):
        await rbac_service.require_classroom_manager(db, principal, classroom.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["owner", "learner", "developer", "student", "guest"])
async def test_classroom_detail_routes_share_the_management_boundary(classroom_roles, key):
    from server.classroom_join.api import get_classroom_join_code, list_classroom_members, list_join_requests
    db, classroom, users = classroom_roles
    principal = await rbac_service.principal_for_user_id(db, users[key].id)
    for endpoint in (get_classroom_join_code, list_classroom_members, list_join_requests):
        if key in {"owner", "developer"}:
            await endpoint(classroom_id=classroom.id, principal=principal, db=db)
        else:
            with pytest.raises(AccessDeniedError):
                await endpoint(classroom_id=classroom.id, principal=principal, db=db)


@pytest.mark.asyncio
async def test_approval_renews_expired_student_role_without_duplicate(classroom_roles):
    from sqlalchemy import func
    from server.classroom_join.api import approve_join_request
    from server.classroom_join.schemas import ApproveJoinRequest
    from server.classroom_join.service import submit_join_request
    from server.infrastructure.mysql.models import RoleModel
    db, classroom, users = classroom_roles
    user = users["guest"]
    student_role = await db.scalar(select(RoleModel.id).where(RoleModel.code == "student"))
    expired = UserRoleModel(user_id=user.id, role_id=student_role, expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    db.add(expired)
    await db.flush()
    request = await submit_join_request(db, classroom.id, user.id, "review001")
    principal = await rbac_service.principal_for_user_id(db, users["owner"].id)
    result = await approve_join_request(classroom_id=classroom.id, request_id=request.id, body=ApproveJoinRequest(), principal=principal, db=db, _write=None)
    await db.flush()
    await db.refresh(expired)
    assert result.status == "approved"
    assert expired.expires_at is None
    assert await db.scalar(select(func.count()).select_from(UserRoleModel).where(UserRoleModel.user_id == user.id, UserRoleModel.role_id == student_role)) == 1
    assert await rbac_service.user_role_codes(db, user.id) == frozenset({"student"})
    assert user.id in {member.user_id for member in await list_active_classroom_members(db, classroom.id)}
