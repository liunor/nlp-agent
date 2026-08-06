"""Tests for user management and workspace isolation."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from db.database import Base, DatabaseManager, database_manager
from models.identity import User, Role, UserRole, AuthSession
from models.agent_state import Workspace, WorkspaceMember, AgentSession, Turn
from crud.user_crud import UserCRUD
from crud.role_crud import RoleCRUD
from crud.auth_session_crud import AuthSessionCRUD
from crud.workspace_crud import WorkspaceCRUD
from crud.agent_session_crud import AgentSessionCRUD
from crud.turn_crud import TurnCRUD
from application.identity.authorization import WorkspacePrincipal, AccessDeniedError
from application.identity.auth_service import AuthService, AuthenticationError
from application.identity.user_service import UserService
from application.identity.workspace_service import WorkspaceService
from application.agent_sessions.service import AgentSessionService
from application.turns.module import TurnApplication


@pytest.fixture(scope="function")
def db_session():
    """Create an in-memory SQLite database for testing."""
    test_db = DatabaseManager("sqlite:///:memory:")
    test_db.create_tables()
    with test_db.session() as session:
        yield session
    test_db.dispose()


@pytest.fixture
def role_crud(db_session):
    return RoleCRUD(db_session)


@pytest.fixture
def user_crud(db_session):
    return UserCRUD(db_session)


@pytest.fixture
def auth_session_crud(db_session):
    return AuthSessionCRUD(db_session)


@pytest.fixture
def workspace_crud(db_session):
    return WorkspaceCRUD(db_session)


@pytest.fixture
def auth_service(db_session):
    return AuthService(db_session)


@pytest.fixture
def user_service(db_session):
    return UserService(db_session)


@pytest.fixture
def workspace_service(db_session):
    return WorkspaceService(db_session)


class TestRoleCRUD:
    def test_create_role(self, role_crud):
        role = role_crud.create(code="test_role", name="Test Role", scope="system")
        assert role.code == "test_role"
        assert role.name == "Test Role"
        assert role.scope == "system"

    def test_get_by_code(self, role_crud):
        role_crud.create(code="admin", name="Admin", scope="system")
        role = role_crud.get_by_code("admin")
        assert role is not None
        assert role.code == "admin"

    def test_ensure_default_roles(self, role_crud):
        roles = role_crud.ensure_default_roles()
        assert "admin" in roles
        assert "teacher" in roles
        assert "learner" in roles
        assert "owner" in roles


class TestUserCRUD:
    def test_create_user(self, user_crud):
        user = user_crud.create(
            username="testuser",
            password_hash="hashed_password",
            display_name="Test User",
            email="test@example.com",
        )
        assert user.username == "testuser"
        assert user.display_name == "Test User"
        assert user.status == "active"

    def test_get_by_username(self, user_crud):
        user_crud.create(
            username="findme",
            password_hash="hash",
            display_name="Find Me",
        )
        user = user_crud.get_by_username("findme")
        assert user is not None
        assert user.username == "findme"

    def test_list_users(self, user_crud):
        user_crud.create(username="user1", password_hash="h", display_name="U1")
        user_crud.create(username="user2", password_hash="h", display_name="U2")
        users, total = user_crud.list_users()
        assert total == 2
        assert len(users) == 2


class TestAuthService:
    def test_create_user_with_password(self, auth_service):
        user = auth_service.create_user(
            username="newuser",
            password="securepassword123",
            display_name="New User",
        )
        assert user.username == "newuser"

    def test_login_success(self, auth_service, role_crud):
        role_crud.ensure_default_roles()
        auth_service.create_user(
            username="loginuser",
            password="mypassword",
            display_name="Login User",
        )
        result = auth_service.login(username="loginuser", password="mypassword")
        assert result.user.username == "loginuser"
        assert result.session_token
        assert result.csrf_token

    def test_login_wrong_password(self, auth_service):
        auth_service.create_user(
            username="wrongpw",
            password="correctpassword",
            display_name="Wrong PW",
        )
        with pytest.raises(AuthenticationError):
            auth_service.login(username="wrongpw", password="wrongpassword")

    def test_login_nonexistent_user(self, auth_service):
        with pytest.raises(AuthenticationError):
            auth_service.login(username="nonexistent", password="password")

    def test_validate_session(self, auth_service, role_crud):
        role_crud.ensure_default_roles()
        auth_service.create_user(
            username="sessionuser",
            password="password123",
            display_name="Session User",
        )
        login_result = auth_service.login(
            username="sessionuser", password="password123"
        )
        validation = auth_service.validate_session(
            session_token=login_result.session_token
        )
        assert validation is not None
        assert validation.user.id == login_result.user.id

    def test_logout_invalidates_session(self, auth_service, role_crud):
        role_crud.ensure_default_roles()
        auth_service.create_user(
            username="logoutuser",
            password="password123",
            display_name="Logout User",
        )
        login_result = auth_service.login(
            username="logoutuser", password="password123"
        )
        auth_service.logout(login_result.auth_session_id)
        validation = auth_service.validate_session(
            session_token=login_result.session_token
        )
        assert validation is None


class TestWorkspaceService:
    def test_create_workspace(self, workspace_service, user_service, role_crud):
        role_crud.ensure_default_roles()
        user = user_service.create_user(
            username="wsowner",
            password="password123",
            display_name="WS Owner",
        )
        workspace = workspace_service.create_workspace(
            name="Test Workspace",
            type="personal",
            created_by_user_id=user.id,
        )
        assert workspace.name == "Test Workspace"
        assert workspace.type == "personal"
        assert workspace_service.is_member(workspace.id, user.id)

    def test_list_user_workspaces(
        self, workspace_service, user_service, role_crud
    ):
        role_crud.ensure_default_roles()
        user = user_service.create_user(
            username="wslister",
            password="password123",
            display_name="WS Lister",
        )
        workspace_service.create_workspace(
            name="WS1", type="personal", created_by_user_id=user.id
        )
        workspace_service.create_workspace(
            name="WS2", type="organization", created_by_user_id=user.id
        )
        workspaces = workspace_service.list_user_workspaces(user.id)
        assert len(workspaces) == 2


class TestWorkspaceIsolation:
    def test_cannot_access_other_workspace_session(
        self, db_session, user_service, workspace_service, role_crud
    ):
        role_crud.ensure_default_roles()
        user1 = user_service.create_user(
            username="user1", password="pass1", display_name="User 1"
        )
        user2 = user_service.create_user(
            username="user2", password="pass2", display_name="User 2"
        )
        ws1 = workspace_service.create_workspace(
            name="WS1", type="personal", created_by_user_id=user1.id
        )
        ws2 = workspace_service.create_workspace(
            name="WS2", type="personal", created_by_user_id=user2.id
        )

        principal1 = WorkspacePrincipal(
            user_id=user1.id,
            auth_session_id="session1",
            workspace_id=ws1.id,
            system_roles=frozenset(),
            workspace_role="owner",
        )
        principal2 = WorkspacePrincipal(
            user_id=user2.id,
            auth_session_id="session2",
            workspace_id=ws2.id,
            system_roles=frozenset(),
            workspace_role="owner",
        )

        session_service = AgentSessionService(db_session)
        session = session_service.create_session(
            principal1, title="User 1's Session"
        )

        result = session_service.get_session(principal2, session.id)
        assert result is None

    def test_admin_can_access_any_workspace(
        self, db_session, user_service, workspace_service, role_crud
    ):
        role_crud.ensure_default_roles()
        regular_user = user_service.create_user(
            username="regular", password="pass", display_name="Regular"
        )
        admin_user = user_service.create_user(
            username="admin", password="adminpass", display_name="Admin"
        )
        # Assign admin role
        user_service.assign_role(admin_user.id, "admin")
        
        ws = workspace_service.create_workspace(
            name="Regular WS", type="personal", created_by_user_id=regular_user.id
        )

        admin_principal = WorkspacePrincipal(
            user_id=admin_user.id,
            auth_session_id="admin_session",
            workspace_id=ws.id,
            system_roles=frozenset({"admin"}),
            workspace_role=None,
        )

        session_service = AgentSessionService(db_session)
        session = session_service.create_session(
            admin_principal, title="Admin Session"
        )
        assert session is not None


class TestTurnApplication:
    def test_submit_turn(
        self, db_session, user_service, workspace_service, role_crud
    ):
        role_crud.ensure_default_roles()
        user = user_service.create_user(
            username="turnuser", password="pass", display_name="Turn User"
        )
        ws = workspace_service.create_workspace(
            name="Turn WS", type="personal", created_by_user_id=user.id
        )

        principal = WorkspacePrincipal(
            user_id=user.id,
            auth_session_id="session",
            workspace_id=ws.id,
            system_roles=frozenset(),
            workspace_role="owner",
        )

        session_service = AgentSessionService(db_session)
        session = session_service.create_session(
            principal, title="Turn Session"
        )

        turn_app = TurnApplication(db_session)
        turn = turn_app.submit_turn(
            principal,
            session.id,
            input_payload={"content": "Hello"},
        )
        assert turn.state == "accepted"
        assert turn.submitted_by_user_id == user.id

    def test_idempotent_turn_submission(
        self, db_session, user_service, workspace_service, role_crud
    ):
        role_crud.ensure_default_roles()
        user = user_service.create_user(
            username="idemuser", password="pass", display_name="Idem User"
        )
        ws = workspace_service.create_workspace(
            name="Idem WS", type="personal", created_by_user_id=user.id
        )

        principal = WorkspacePrincipal(
            user_id=user.id,
            auth_session_id="session",
            workspace_id=ws.id,
            system_roles=frozenset(),
            workspace_role="owner",
        )

        session_service = AgentSessionService(db_session)
        session = session_service.create_session(
            principal, title="Idem Session"
        )

        turn_app = TurnApplication(db_session)
        turn1 = turn_app.submit_turn(
            principal,
            session.id,
            input_payload={"content": "Hello"},
            idempotency_key="unique-key-123",
        )
        turn2 = turn_app.submit_turn(
            principal,
            session.id,
            input_payload={"content": "Hello"},
            idempotency_key="unique-key-123",
        )
        assert turn1.id == turn2.id
