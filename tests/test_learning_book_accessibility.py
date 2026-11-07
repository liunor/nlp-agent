"""Tests for guest and student knowledge book accessibility and published workspace fallback."""

import asyncio
import pytest

from core.identity import AuthenticatedPrincipal
from core.learning import KnowledgeBookContext
from core.rbac import AccessDeniedError, Permission
from core.session_context import SessionContext
from gateway import core as gateway_core
from gateway.core import BackendGateway
from gateway.repository import GatewayRepository
from server.teacher.service import teacher_service


class FakeEngine:
    async def start(self, event_sink):
        pass

    async def run_turn(self, context, turn_id, content):
        pass


class FakeSessions:
    pass


class RecordingTurnDispatcher:
    pass


@pytest.fixture
def test_setup(tmp_path):
    repo = GatewayRepository(tmp_path / "gateway.sqlite3")
    gateway = BackendGateway(
        engine=FakeEngine(),
        repository=repo,
        sessions=FakeSessions(),
        dispatcher=RecordingTurnDispatcher(),
    )

    # Seed published knowledge book into "platform-ws"
    catalog = {
        "workspace_id": "platform-ws",
        "topics": [
            {
                "id": "topic-1",
                "name": "NLP 基础",
                "description": "入门介绍",
                "status": "enabled",
                "knowledge_points": [
                    {
                        "id": "point-tensor",
                        "name": "张量基础",
                        "markdown": "# 默认草稿",
                        "status": "enabled",
                        "sort_order": 0,
                        "question_types": ["简答"],
                    }
                ],
            }
        ],
        "exercise_blueprints": [],
        "review_blueprints": [],
        "guided_blueprints": [],
    }
    repo.update_teaching_catalog("platform-ws", catalog)
    repo.apply_knowledge_book_import(
        "platform-ws",
        [],
        [
            {
                "asset_path": "assets/pages/point-tensor/flow.png",
                "media_type": "image/png",
                "content": b"\x89PNG\r\n\x1a\nfake-png-content",
                "sha256": "fake-sha256",
            }
        ],
    )
    repo.update_knowledge_page("platform-ws", "point-tensor", "# 张量与计算图\n\n![架构图](/assets/assets/pages/point-tensor/flow.png)")
    repo.publish_knowledge_page("platform-ws", "point-tensor", expected_revision=1)
    file_row = repo.create_knowledge_book_file(
        workspace_id="platform-ws",
        knowledge_point_id="point-tensor",
        original_name="demo.py",
        display_name="张量示例.py",
        media_type="text/x-python",
        content=b"import torch\nprint(torch.__version__)",
        created_by="teacher-1",
    )
    repo.set_knowledge_book_file_refs("platform-ws", "point-tensor", "draft", [file_row["id"]])
    repo.publish_knowledge_book_file_refs("platform-ws", "point-tensor")

    return repo, gateway, file_row["id"]


@pytest.mark.asyncio
async def test_guest_can_read_published_knowledge_book(test_setup):
    repo, gateway, file_id = test_setup

    guest = AuthenticatedPrincipal(
        user_id="guest-1",
        workspace_ids=frozenset({"guest-ws"}),
        roles=frozenset({"guest"}),
        permissions=frozenset({Permission.LEARNING_CONTENT_READ_PUBLIC}),
    )

    # 1. Navigation succeeds and returns items from published workspace
    nav = await teacher_service.learning_book_navigation(guest, gateway, "guest-ws")
    assert nav["workspace_id"] == "guest-ws"
    assert len(nav["items"]) == 1
    assert nav["items"][0]["knowledge_point_id"] == "point-tensor"
    assert nav["items"][0]["title"] == "张量基础"

    # 2. Page content succeeds and stamped with guest workspace_id
    page_result = await teacher_service.learning_book_page(guest, gateway, "guest-ws", "point-tensor")
    page = page_result["page"]
    assert page["workspace_id"] == "guest-ws"
    assert page["title"] == "张量基础"
    assert "张量与计算图" in page["content_markdown"]
    assert len(page["files"]) == 1
    assert page["files"][0]["display_name"] == "张量示例.py"

    # 3. File read succeeds
    file_result = await teacher_service.learning_book_file(guest, gateway, "guest-ws", file_id)
    assert file_result["content"] == b"import torch\nprint(torch.__version__)"

    # 4. Asset read succeeds
    asset = await teacher_service.knowledge_book_asset(
        guest, gateway, "guest-ws", "assets/pages/point-tensor/flow.png"
    )
    assert asset["content"] == b"\x89PNG\r\n\x1a\nfake-png-content"

    # 5. Published teaching catalog succeeds for guest
    pub_cat = await gateway.get_published_teaching_catalog(guest, "guest-ws")
    assert len(pub_cat["catalog"]["topics"]) == 1
    assert pub_cat["catalog"]["topics"][0]["id"] == "topic-1"

    # 6. Direct teaching catalog query on gateway still rejects guest
    with pytest.raises(AccessDeniedError, match="learning:content:read_workspace"):
        await gateway.get_teaching_catalog(guest, "guest-ws")


@pytest.mark.asyncio
async def test_student_in_empty_workspace_falls_back_to_published_content(test_setup):
    repo, gateway, file_id = test_setup

    student = AuthenticatedPrincipal(
        user_id="student-1",
        workspace_ids=frozenset({"student-ws-empty"}),
        roles=frozenset({"student"}),
        permissions=frozenset({
            Permission.LEARNING_CONTENT_READ_PUBLIC,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
        }),
    )

    # Navigation for student's empty workspace
    nav = await teacher_service.learning_book_navigation(student, gateway, "student-ws-empty")
    assert nav["workspace_id"] == "student-ws-empty"
    assert len(nav["items"]) == 1
    assert nav["items"][0]["knowledge_point_id"] == "point-tensor"

    # Page for student's empty workspace
    page_result = await teacher_service.learning_book_page(student, gateway, "student-ws-empty", "point-tensor")
    page = page_result["page"]
    assert page["workspace_id"] == "student-ws-empty"
    assert "张量与计算图" in page["content_markdown"]

    # Student asking Nova about this page
    candidate_context = KnowledgeBookContext(
        workspace_id="student-ws-empty",
        topic_id="topic-1",
        knowledge_point_id="point-tensor",
        content_markdown="",
    )
    resolved_context = await gateway_core._resolve_knowledge_book_context(
        repo,
        SessionContext(session_id="sess-1", workspace_id="student-ws-empty"),
        candidate_context,
    )
    assert resolved_context is not None
    assert "张量与计算图" in resolved_context.content_markdown

    # Published catalog fallback for student
    pub_cat = await gateway.get_published_teaching_catalog(student, "student-ws-empty")
    assert len(pub_cat["catalog"]["topics"]) == 1
    assert pub_cat["catalog"]["topics"][0]["id"] == "topic-1"


@pytest.mark.asyncio
async def test_classroom_published_workspace_priority(test_setup):
    repo, gateway, _ = test_setup

    # Create classroom workspace with customized textbook
    classroom_catalog = {
        "workspace_id": "classroom-ws",
        "topics": [
            {
                "id": "topic-class",
                "name": "一班专属课程",
                "description": "定制",
                "status": "enabled",
                "knowledge_points": [
                    {
                        "id": "point-class-1",
                        "name": "一班张量重点",
                        "markdown": "",
                        "status": "enabled",
                        "sort_order": 0,
                        "question_types": ["简答"],
                    }
                ],
            }
        ],
        "exercise_blueprints": [],
        "review_blueprints": [],
        "guided_blueprints": [],
    }
    repo.update_teaching_catalog("classroom-ws", classroom_catalog)
    repo.update_knowledge_page("classroom-ws", "point-class-1", "# 一班定制教材内容")
    repo.publish_knowledge_page("classroom-ws", "point-class-1", expected_revision=1)

    student_in_class = AuthenticatedPrincipal(
        user_id="student-2",
        workspace_ids=frozenset({"student-personal-ws", "classroom-ws"}),
        classroom_ids=frozenset({"class-1"}),
        roles=frozenset({"student"}),
        permissions=frozenset({
            Permission.LEARNING_CONTENT_READ_PUBLIC,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
        }),
    )

    # When student is in personal workspace, it resolves to classroom-ws because student belongs to it
    nav = await teacher_service.learning_book_navigation(student_in_class, gateway, "student-personal-ws")
    assert nav["workspace_id"] == "student-personal-ws"
    assert len(nav["items"]) == 1
    assert nav["items"][0]["knowledge_point_id"] == "point-class-1"
    assert nav["items"][0]["title"] == "一班张量重点"


def test_developer_and_admin_bypass_workspace_check():
    dev = AuthenticatedPrincipal(
        user_id="dev-user",
        workspace_ids=frozenset({"any-ws"}),
        roles=frozenset({"developer"}),
    )
    # Developer can access any arbitrary workspace without error
    dev.require_workspace("completely-unknown-workspace-xyz")

    admin = AuthenticatedPrincipal(
        user_id="admin-user",
        workspace_ids=frozenset({"any-ws"}),
        roles=frozenset({"admin"}),
    )
    admin.require_workspace("completely-unknown-workspace-xyz")

    student = AuthenticatedPrincipal(
        user_id="student-user",
        workspace_ids=frozenset({"my-workspace"}),
        roles=frozenset({"student"}),
    )
    student.require_workspace("my-workspace")
    with pytest.raises(AccessDeniedError):
        student.require_workspace("other-workspace")
