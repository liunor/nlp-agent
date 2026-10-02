"""Transaction-rolled-back integration checks for permanent user deletion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from configs.settings import settings
from server.infrastructure.mysql import MySQLRuntime
from server.infrastructure.mysql.models import (
    AuthorizationAuditLogModel, ConversationModel, ConversationTranscriptModel,
    CourseCatalogModel, KnowledgeBookFileModel, MemoryDocumentModel,
    OutboxMessageModel, SessionModel, TurnModel, UserModel,
    WorkspaceMemberModel, WorkspaceModel, WsTicketModel,
)
from server.user.schemas import UserCreate
from server.user.service import HardDeleteBlockedError, UserService
from server.user.local_cleanup import LocalCleanup, schedule_local_cleanup


@pytest.fixture
async def session():
    if not settings.NLP_AGENT_DATABASE_URL:
        pytest.skip("MySQL integration database is not configured")
    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory() as db:
            yield db
            await db.rollback()  # Never persist the synthetic users to the local database.
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_hard_delete_removes_account_and_personal_workspace(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}"))
    auth_session_id = str(uuid4())
    session.add(SessionModel(
        id=auth_session_id, user_id=owner.id, workspace_id=workspace_id,
        token_hash=f"token-{uuid4()}", csrf_hash=f"csrf-{uuid4()}",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    ))
    await session.flush()
    session.add(WsTicketModel(
        id=str(uuid4()), auth_session_id=auth_session_id, user_id=owner.id,
        workspace_id=workspace_id, ticket_hash=f"ticket-{uuid4()}", origin="http://localhost",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
    ))
    session.add(MemoryDocumentModel(
        id=str(uuid4()), user_id=owner.id, workspace_id=workspace_id,
        scope="user", document_key="test", content_json={"secret": "mine"}, revision=1,
    ))
    session.add(AuthorizationAuditLogModel(
        id=str(uuid4()), actor_user_id=other.id, target_user_id=owner.id,
        decision="allow", reason_code="test", resource_type="user", resource_id=owner.id,
        detail_json={},
    ))
    conversation_id = str(uuid4())
    turn_id = str(uuid4())
    session.add(ConversationModel(
        id=conversation_id, owner_user_id=owner.id, workspace_id=workspace_id,
        title="Private conversation", channel="web", status="active",
    ))
    await session.flush()
    session.add(TurnModel(
        id=turn_id, conversation_id=conversation_id, workspace_id=workspace_id,
        user_id=owner.id, input_text="Sensitive message", status="completed",
    ))
    session.add(ConversationTranscriptModel(
        id=str(uuid4()), session_id=conversation_id, message_uuid=str(uuid4()),
        message_type="human", role="user", content_json={"text": "Sensitive message"},
    ))
    session.add(OutboxMessageModel(
        id=str(uuid4()), topic="turn.dispatch", payload_json={"turn_id": turn_id},
    ))
    await session.flush()

    await service.hard_delete_user(owner.id, actor_user_id=other.id)

    assert await session.get(UserModel, owner.id) is None
    assert await session.get(WorkspaceModel, workspace_id) is None
    assert await session.get(SessionModel, auth_session_id) is None
    assert await session.scalar(select(WsTicketModel.id).where(WsTicketModel.user_id == owner.id)) is None
    assert await session.scalar(select(MemoryDocumentModel.id).where(MemoryDocumentModel.user_id == owner.id)) is None
    assert await session.scalar(select(AuthorizationAuditLogModel.id).where(
        AuthorizationAuditLogModel.resource_id == owner.id
    )) is None
    assert await session.get(ConversationModel, conversation_id) is None
    assert await session.scalar(select(ConversationTranscriptModel.id).where(
        ConversationTranscriptModel.session_id == conversation_id
    )) is None
    assert await session.scalar(select(OutboxMessageModel.id).where(
        OutboxMessageModel.payload_json["turn_id"].as_string() == turn_id
    )) is None
    assert await session.get(UserModel, other.id) is not None
    replacement = await service.create_user(UserCreate(
        username=name, display_name=name, password="password123"
    ))
    assert replacement.id != owner.id
    assert await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}")) is not None


@pytest.mark.asyncio
async def test_hard_delete_rejects_shared_personal_workspace(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}"))
    session.add(WorkspaceMemberModel(workspace_id=workspace_id, user_id=other.id, member_type="member", status="active"))
    await session.flush()

    with pytest.raises(HardDeleteBlockedError, match="other users"):
        await service.hard_delete_user(owner.id, actor_user_id=other.id)

    assert await session.get(UserModel, owner.id) is not None
    assert await session.get(WorkspaceModel, workspace_id) is not None


@pytest.mark.asyncio
async def test_hard_delete_rejects_other_users_files_in_personal_workspace(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}"))
    session.add(CourseCatalogModel(workspace_id=workspace_id))
    await session.flush()
    session.add(KnowledgeBookFileModel(
        id=str(uuid4()), workspace_id=workspace_id, knowledge_point_id=str(uuid4()),
        original_name="shared.txt", display_name="shared.txt", media_type="text/plain",
        content=b"other", size_bytes=5, sha256="0" * 64, created_by=other.id,
    ))
    await session.flush()

    with pytest.raises(HardDeleteBlockedError, match="other users' files"):
        await service.hard_delete_user(owner.id, actor_user_id=other.id)
    assert await session.get(UserModel, owner.id) is not None
    assert await session.get(WorkspaceModel, workspace_id) is not None


@pytest.mark.asyncio
async def test_hard_delete_rejects_active_turn(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}"))
    conversation_id = str(uuid4())
    session.add(ConversationModel(
        id=conversation_id, owner_user_id=owner.id, workspace_id=workspace_id,
        title="In progress", channel="web", status="active",
    ))
    await session.flush()
    session.add(TurnModel(
        id=str(uuid4()), conversation_id=conversation_id, workspace_id=workspace_id,
        user_id=owner.id, input_text="Active message", status="running",
    ))
    await session.flush()

    with pytest.raises(HardDeleteBlockedError, match="active conversation"):
        await service.hard_delete_user(owner.id, actor_user_id=other.id)
    assert await session.get(UserModel, owner.id) is not None


@pytest.mark.asyncio
async def test_hard_delete_rejects_other_users_turns_in_owned_conversation(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{other.username}"))
    conversation_id = str(uuid4())
    session.add(ConversationModel(
        id=conversation_id, owner_user_id=owner.id, workspace_id=workspace_id,
        title="Shared conversation", channel="web", status="active",
    ))
    await session.flush()
    session.add(TurnModel(
        id=str(uuid4()), conversation_id=conversation_id, workspace_id=workspace_id,
        user_id=other.id, input_text="Other user's data", status="completed",
    ))
    await session.flush()

    with pytest.raises(HardDeleteBlockedError, match="other users' data"):
        await service.hard_delete_user(owner.id, actor_user_id=other.id)
    assert await session.get(UserModel, owner.id) is not None
    assert await session.get(ConversationModel, conversation_id) is not None


@pytest.mark.asyncio
async def test_hard_delete_rejects_users_turns_in_another_conversation(session):
    service = UserService(session)
    name = f"purge{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    other = await service.create_user(UserCreate(
        username=f"admin{uuid4().hex[:10]}", display_name="Admin", password="password123"
    ))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{other.username}"))
    conversation_id = str(uuid4())
    session.add(ConversationModel(
        id=conversation_id, owner_user_id=other.id, workspace_id=workspace_id,
        title="Other's conversation", channel="web", status="active",
    ))
    await session.flush()
    session.add(TurnModel(
        id=str(uuid4()), conversation_id=conversation_id, workspace_id=workspace_id,
        user_id=owner.id, input_text="Sensitive data", status="completed",
    ))
    await session.flush()

    with pytest.raises(HardDeleteBlockedError, match="other users' conversations"):
        await service.hard_delete_user(owner.id, actor_user_id=other.id)
    assert await session.get(UserModel, owner.id) is not None
    assert await session.get(ConversationModel, conversation_id) is not None


@pytest.mark.asyncio
async def test_self_delete_account_removes_own_account(session):
    """Self-service deletion removes the account and its personal workspace."""
    service = UserService(session)
    name = f"selfdel{uuid4().hex[:10]}"
    owner = await service.create_user(UserCreate(username=name, display_name=name, password="password123"))
    workspace_id = await session.scalar(select(WorkspaceModel.id).where(WorkspaceModel.slug == f"user-{name}"))
    await session.flush()

    await service.self_delete_account(owner.id)

    assert await session.get(UserModel, owner.id) is None
    assert await session.get(WorkspaceModel, workspace_id) is None


@pytest.mark.asyncio
async def test_self_delete_account_rejects_last_developer(session):
    """Self-service deletion cannot remove the final active developer."""
    service = UserService(session)
    from server.rbac.service import rbac_service

    name = f"dev{uuid4().hex[:10]}"
    developer = await service.create_user(
        UserCreate(username=name, display_name=name, password="password123")
    )
    # Assign the developer role so the last-developer guard is exercised.
    await rbac_service.replace_user_roles(
        session, user_id=developer.id, role_codes={"developer"}, assigned_by_user_id=None
    )
    await session.flush()

    from server.user.service import LastDeveloperForbiddenError

    with pytest.raises(LastDeveloperForbiddenError):
        await service.self_delete_account(developer.id)

    assert await session.get(UserModel, developer.id) is not None


@pytest.mark.asyncio
async def test_local_cleanup_runs_after_commit_only(tmp_path, monkeypatch):
    from server.user import local_cleanup

    monkeypatch.setattr(local_cleanup, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(local_cleanup, "DEFAULT_UPLOADS_ROOT", tmp_path / "uploads")
    upload = tmp_path / "uploads" / "workspace" / "user" / "attachment.png"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"private")
    plan = LocalCleanup("user", ("workspace",), None, (), ())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with AsyncSession(engine) as db:
            async with db.begin():
                schedule_local_cleanup(db, plan)
                await db.rollback()
            assert upload.exists()
            async with db.begin():
                pass
            assert upload.exists()
        async with AsyncSession(engine) as db:
            async with db.begin():
                schedule_local_cleanup(db, plan)
        assert not upload.exists()
    finally:
        await engine.dispose()
