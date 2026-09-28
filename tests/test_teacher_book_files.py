import hashlib

import pytest

from gateway.repository import GatewayRepository
from server.teacher.files import (
    MAX_KNOWLEDGE_BOOK_FILE_BYTES,
    extract_knowledge_book_file_ids,
    file_token,
    validate_knowledge_book_file,
)


def test_validate_knowledge_book_file_returns_safe_metadata() -> None:
    content = b"print('hello')\n"

    metadata = validate_knowledge_book_file(
        "attention_demo.py",
        "text/x-python",
        content,
    )

    assert metadata == {
        "original_name": "attention_demo.py",
        "display_name": "attention_demo.py",
        "media_type": "text/x-python",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_validate_knowledge_book_file_rejects_unsafe_or_unsupported_input() -> None:
    assert MAX_KNOWLEDGE_BOOK_FILE_BYTES == 10 * 1024 * 1024
    accepted = validate_knowledge_book_file("ten-megabytes.txt", "text/plain", b"x" * MAX_KNOWLEDGE_BOOK_FILE_BYTES)
    assert accepted["size_bytes"] == MAX_KNOWLEDGE_BOOK_FILE_BYTES
    with pytest.raises(ValueError, match="文件名不能包含路径"):
        validate_knowledge_book_file("../secret.py", "text/x-python", b"x")

    with pytest.raises(ValueError, match="不支持的教材文件类型"):
        validate_knowledge_book_file("program.exe", "application/octet-stream", b"x")

    with pytest.raises(ValueError, match="文件不能超过"):
        validate_knowledge_book_file(
            "large.txt",
            "text/plain",
            b"x" * (MAX_KNOWLEDGE_BOOK_FILE_BYTES + 1),
        )


def test_validate_knowledge_book_file_normalizes_generic_mime_only_for_text_extensions() -> None:
    metadata = validate_knowledge_book_file(
        "attention_demo.py",
        "application/octet-stream",
        b"print('hello')\n",
    )

    assert metadata["media_type"] == "text/plain"

    with pytest.raises(ValueError, match="不支持的教材文件类型"):
        validate_knowledge_book_file("payload", "application/octet-stream", b"\x00\x01")

    with pytest.raises(ValueError, match="必须是可预览的文本类型"):
        validate_knowledge_book_file("attention_demo.py", "application/octet-stream", b"\x00\x01")


def test_file_tokens_are_deduplicated_and_keep_document_order() -> None:
    first = "550e8400-e29b-41d4-a716-446655440000"
    second = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    markdown = f"[a](book-file:{first})\n\n[b](book-file:{second})\n\n[a again](book-file:{first})"

    assert extract_knowledge_book_file_ids(markdown) == [first, second]
    assert file_token(first) == f"book-file:{first}"


def test_gateway_repository_crud_and_draft_published_file_refs(tmp_path) -> None:
    repository = GatewayRepository(tmp_path / "gateway.sqlite3")
    content = b"print('hello')\n"

    created = repository.create_knowledge_book_file(
        workspace_id="workspace-1",
        knowledge_point_id="point-1",
        original_name="demo.py",
        display_name="演示代码.py",
        media_type="text/x-python",
        content=content,
        created_by="teacher-1",
        file_id="file-1",
    )

    assert created["id"] == "file-1"
    assert created["content"] == content
    assert repository.list_knowledge_book_files("workspace-1", "point-1")[0]["id"] == "file-1"

    updated = repository.update_knowledge_book_file(
        "workspace-1",
        "file-1",
        display_name="新的名字.py",
    )
    assert updated["display_name"] == "新的名字.py"

    repository.set_knowledge_book_file_refs(
        "workspace-1", "point-1", "draft", ["file-1"]
    )
    assert repository.list_knowledge_book_file_refs("workspace-1", "point-1", "draft") == ["file-1"]
    assert repository.get_published_knowledge_book_file("workspace-1", "file-1") is None

    assert repository.publish_knowledge_book_file_refs("workspace-1", "point-1") == ["file-1"]
    published = repository.get_published_knowledge_book_file("workspace-1", "file-1")
    assert published is not None
    assert published["content"] == content

    with pytest.raises(ValueError, match="已发布文件不能原地更新"):
        repository.update_knowledge_book_file(
            "workspace-1", "file-1", content=b"changed"
        )

    with pytest.raises(ValueError, match="仍被教材引用"):
        repository.delete_knowledge_book_file("workspace-1", "file-1")

    repository.set_knowledge_book_file_refs(
        "workspace-1", "point-1", "draft", []
    )
    assert repository.publish_knowledge_book_file_refs("workspace-1", "point-1") == []
    assert repository.delete_knowledge_book_file("workspace-1", "file-1") is True
    assert repository.get_knowledge_book_file("workspace-1", "file-1") is None
    repository.close()
