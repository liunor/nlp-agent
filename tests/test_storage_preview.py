from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.storage import service as storage_service
from server.storage.service import StorageValidationError


def _workspace_file(name: str, content: bytes) -> Path:
    path = Path.cwd() / f".storage-preview-{uuid4().hex}-{name}"
    path.write_bytes(content)
    return path


def test_text_preview_reads_only_the_configured_prefix() -> None:
    path = _workspace_file("lesson.md", b"abcdefghij")

    try:
        preview = storage_service.build_text_preview(path, display_name="lesson.md", mime_type="text/markdown", max_bytes=4)
    finally:
        path.unlink(missing_ok=True)

    assert preview == {
        "content": "abcd",
        "truncated": True,
        "mime_type": "text/markdown",
        "bytes_read": 4,
    }


def test_binary_file_is_rejected_instead_of_being_decoded_as_text() -> None:
    path = _workspace_file("slides.pdf", b"%PDF-1.7\x00binary")

    try:
        with pytest.raises(StorageValidationError, match="暂不支持在线预览"):
            storage_service.build_text_preview(path, display_name="slides.pdf", mime_type="application/pdf")
    finally:
        path.unlink(missing_ok=True)


def test_readme_without_an_extension_uses_the_text_preview() -> None:
    path = _workspace_file("README", b"setup instructions")

    try:
        preview = storage_service.build_text_preview(
            path,
            display_name="README",
            mime_type="application/octet-stream",
        )
    finally:
        path.unlink(missing_ok=True)

    assert preview["content"] == "setup instructions"
