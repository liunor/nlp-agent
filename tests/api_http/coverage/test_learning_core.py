"""Student learning-content contracts and workspace isolation."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from ..support.http import json_response
from ..support.resources import (
    add_workspace_member,
    create_workspace,
    seed_catalog,
)


pytestmark = pytest.mark.api_core


def _png_base64() -> str:
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color=(100, 150, 200)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def _learning_context(authenticated_client_for, teacher_user, student_user):
    teacher = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher, name="Phase 4 Learning Workspace")
    add_workspace_member(
        teacher,
        workspace_id=workspace["id"],
        user_id=student_user.user_id,
    )
    student = authenticated_client_for(student_user)
    catalog = seed_catalog(
        teacher,
        workspace_id=workspace["id"],
        prefix="phase4learning",
    )
    return teacher, student, workspace, catalog


def test_student_can_read_published_catalog_navigation_and_page(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, catalog = _learning_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )

    student_catalog = json_response(
        student.get(f"/api/v1/learning/catalog/{workspace['id']}"),
        200,
    )
    assert student_catalog["catalog"]["workspace_id"] == workspace["id"]
    assert student_catalog["catalog"]["topics"][0]["id"] == catalog.topic_id
    assert student_catalog["catalog"]["topics"][0]["knowledge_points"][0]["id"] == catalog.knowledge_point_id

    draft = teacher.put(
        f"/api/v1/teacher/book/{workspace['id']}/pages/{catalog.knowledge_point_id}",
        json={"content_markdown": "# Draft\n\nPrivate draft", "expected_revision": 0},
    )
    assert draft.status_code == 200, draft.text
    assert student.get(
        f"/api/v1/learning/book/{workspace['id']}/pages/{catalog.knowledge_point_id}"
    ).status_code == 404

    navigation_before = json_response(
        teacher.get(f"/api/v1/teacher/book/{workspace['id']}/navigation"),
        200,
    )
    assert navigation_before["items"][0]["has_draft"] is True
    assert navigation_before["items"][0]["has_published"] is False

    published = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/pages/{catalog.knowledge_point_id}/publish",
        json={"expected_revision": 1},
    )
    assert published.status_code == 200, published.text
    page = json_response(
        student.get(
            f"/api/v1/learning/book/{workspace['id']}/pages/{catalog.knowledge_point_id}"
        ),
        200,
    )
    assert page["page"]["workspace_id"] == workspace["id"]
    assert page["page"]["knowledge_point_id"] == catalog.knowledge_point_id
    assert page["page"]["content_markdown"] == "# Draft\n\nPrivate draft"

    learning_navigation = json_response(
        student.get(f"/api/v1/learning/book/{workspace['id']}/navigation"),
        200,
    )
    assert learning_navigation["items"][0]["knowledge_point_id"] == catalog.knowledge_point_id
    assert learning_navigation["items"][0]["revision"] == 1


def test_learning_page_asset_is_private_and_not_exposed_before_publish(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, catalog = _learning_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    saved = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/apply",
        json={
            "knowledge_point_id": catalog.knowledge_point_id,
            "file_name": "asset-page.md",
            "content_markdown": "# Page\n\n![figure](assets/figure.png)",
            "expected_revision": 0,
            "assets": [
                {
                    "asset_path": "assets/figure.png",
                    "media_type": "image/png",
                    "content_base64": _png_base64(),
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    stored_markdown = saved.json()["page"]["draft_markdown"]
    asset_path = stored_markdown.split("/assets/", 1)[1].split(")", 1)[0]
    asset_url = f"/api/v1/learning/book/{workspace['id']}/assets/{asset_path}"
    assert student.get(asset_url).status_code == 404

    published = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/pages/{catalog.knowledge_point_id}/publish",
        json={"expected_revision": 1},
    )
    assert published.status_code == 200, published.text
    asset = student.get(asset_url)
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/png")
    assert asset.content == base64.b64decode(_png_base64())


def test_learning_resources_are_isolated_from_another_workspace(
    authenticated_client_for,
    developer_user,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace_a, catalog = _learning_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    other_developer = authenticated_client_for(developer_user)
    workspace_b = create_workspace(other_developer, name="Phase 4 Other Learning Workspace")

    response = student.get(f"/api/v1/learning/catalog/{workspace_b['id']}")
    assert response.status_code == 403
    assert student.get(
        f"/api/v1/learning/book/{workspace_b['id']}/navigation"
    ).status_code == 403
    assert student.get(
        f"/api/v1/learning/book/{workspace_a['id']}/pages/unknown-point"
    ).status_code == 404
    assert teacher.get(
        f"/api/v1/learning/catalog/{workspace_b['id']}"
    ).status_code == 403


def test_published_release_notes_are_public_to_authenticated_learners(
    authenticated_client,
    student_user,
) -> None:
    del student_user
    response = authenticated_client.get("/api/v1/learning/release-notes")
    payload = json_response(response, 200)
    assert isinstance(payload, dict)
    assert isinstance(payload["items"], list)
