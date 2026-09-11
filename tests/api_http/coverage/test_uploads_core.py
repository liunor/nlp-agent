"""Real HTTP upload safety and ownership checks."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ..support.resources import create_session


pytestmark = pytest.mark.api_core


def _image_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), color=(20, 80, 140)).save(stream, format="PNG")
    return stream.getvalue()


def _session_dir(
    uploads_root: Path,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> Path:
    return uploads_root / workspace_id / user_id / session_id


def _remove_session_dir(
    uploads_root: Path,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> None:
    shutil.rmtree(
        _session_dir(uploads_root, workspace_id, user_id, session_id),
        ignore_errors=True,
    )


def test_valid_image_upload_download_and_cleanup(
    authenticated_client,
    student_user,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    session_id = session["session_id"]
    try:
        content = _image_bytes()
        uploaded = authenticated_client.post(
            "/api/v1/uploads",
            data={"session_id": session_id},
            files={"file": ("profile.png", content, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        payload = uploaded.json()
        assert payload["url"].endswith(f"/{payload['file_name']}")
        assert payload["media_type"] == "image/png"
        assert payload["width"] == 64
        assert payload["height"] == 64
        assert payload["size_bytes"] == len(content)
        assert len(payload["sha256"]) == 64

        downloaded = authenticated_client.get(payload["url"])
        assert downloaded.status_code == 200
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert downloaded.content == content
    finally:
        _remove_session_dir(
            api_http_environment.uploads_root,
            student_user.workspace_id,
            student_user.user_id,
            session_id,
        )


def test_empty_and_invalid_image_files_are_rejected(
    authenticated_client,
    student_user,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    session_id = session["session_id"]
    try:
        empty = authenticated_client.post(
            "/api/v1/uploads",
            data={"session_id": session_id},
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert empty.status_code == 415

        invalid = authenticated_client.post(
            "/api/v1/uploads",
            data={"session_id": session_id},
            files={"file": ("not-image.txt", b"not an image", "text/plain")},
        )
        assert invalid.status_code == 415
    finally:
        _remove_session_dir(
            api_http_environment.uploads_root,
            student_user.workspace_id,
            student_user.user_id,
            session_id,
        )


def test_upload_filename_is_not_used_as_a_filesystem_path(
    authenticated_client,
    student_user,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    session_id = session["session_id"]
    try:
        response = authenticated_client.post(
            "/api/v1/uploads",
            data={"session_id": session_id},
            files={"file": ("..\\..\\outside.png", _image_bytes(), "image/png")},
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert ".." not in payload["file_name"]
        assert "/" not in payload["file_name"]
        assert "\\" not in payload["file_name"]
    finally:
        _remove_session_dir(
            api_http_environment.uploads_root,
            student_user.workspace_id,
            student_user.user_id,
            session_id,
        )


def test_missing_and_traversal_downloads_are_hidden(
    authenticated_client,
    student_user,
    api_http_environment,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    session_id = session["session_id"]
    try:
        assert authenticated_client.get(
            f"/api/v1/uploads/{session_id}/missing.png"
        ).status_code == 404
        for escaped in ("%2e%2e%2f%2e%2e%2fetc%2fpasswd", "%2e%2e%5c%2e%2e%5csecret"):
            assert authenticated_client.get(
                f"/api/v1/uploads/{session_id}/{escaped}"
            ).status_code == 404
    finally:
        _remove_session_dir(
            api_http_environment.uploads_root,
            student_user.workspace_id,
            student_user.user_id,
            session_id,
        )


def test_upload_is_scoped_to_the_owner_and_workspace(
    authenticated_client_for,
    student_user,
    developer_user,
    api_http_environment,
) -> None:
    owner = authenticated_client_for(student_user)
    other = authenticated_client_for(developer_user)
    owner_session = create_session(owner, workspace_id=student_user.workspace_id)
    other_session = create_session(other, workspace_id=developer_user.workspace_id)
    owner_id = owner_session["session_id"]
    other_id = other_session["session_id"]
    try:
        upload = owner.post(
            "/api/v1/uploads",
            data={"session_id": owner_id},
            files={"file": ("owner.png", _image_bytes(), "image/png")},
        )
        assert upload.status_code == 201, upload.text
        owner_url = upload.json()["url"]
        assert other.get(owner_url).status_code == 404

        other_upload = other.post(
            "/api/v1/uploads",
            data={"session_id": other_id},
            files={"file": ("other.png", _image_bytes(), "image/png")},
        )
        assert other_upload.status_code == 201, other_upload.text
        assert owner.get(other_upload.json()["url"]).status_code == 404
    finally:
        _remove_session_dir(
            api_http_environment.uploads_root,
            student_user.workspace_id,
            student_user.user_id,
            owner_id,
        )
        _remove_session_dir(
            api_http_environment.uploads_root,
            developer_user.workspace_id,
            developer_user.user_id,
            other_id,
        )
