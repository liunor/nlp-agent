"""Domain rules for files embedded in teacher-authored knowledge books."""

from __future__ import annotations

import hashlib
import re
from typing import Final


MAX_KNOWLEDGE_BOOK_FILE_BYTES: Final = 10 * 1024 * 1024
MAX_KNOWLEDGE_BOOK_FILE_NAME_LENGTH: Final = 255

# Keep this allow-list aligned with the existing local Files tool.  These are
# all safe to preview as escaped text or Markdown; the renderer must never
# execute HTML/SVG content from a course file.
_SUPPORTED_EXTENSIONS: Final = frozenset(
    {
        "md",
        "markdown",
        "mdown",
        "mkd",
        "txt",
        "text",
        "log",
        "csv",
        "tsv",
        "js",
        "mjs",
        "cjs",
        "jsx",
        "ts",
        "tsx",
        "py",
        "json",
        "css",
        "scss",
        "html",
        "htm",
        "xml",
        "yaml",
        "yml",
        "sh",
        "bash",
        "zsh",
        "sql",
        "java",
        "c",
        "h",
        "cpp",
        "hpp",
        "cs",
        "go",
        "rs",
        "rb",
        "php",
        "swift",
        "kt",
        "vue",
        "svelte",
        "toml",
        "ini",
        "env",
    }
)
_SUPPORTED_FILENAMES: Final = frozenset({"dockerfile", "makefile"})

_FILE_TOKEN_RE: Final = re.compile(
    r"book-file:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def validate_knowledge_book_file_name(name: str, *, label: str = "教材文件名") -> str:
    """Validate a user-visible file name without inspecting its contents."""

    if not name or len(name) > MAX_KNOWLEDGE_BOOK_FILE_NAME_LENGTH:
        raise ValueError(f"{label}不能为空且不能超过 255 个字符")
    if any(character in name for character in ("/", "\\", "\x00")):
        raise ValueError(f"{label}不能包含路径")
    if any(ord(character) < 32 for character in name):
        raise ValueError(f"{label}包含不可用控制字符")
    return name


def _is_text_media_type(media_type: str) -> bool:
    normalized = media_type.split(";", 1)[0].strip().lower()
    return normalized.startswith("text/") or normalized in {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-javascript",
        "application/sql",
    }


def validate_knowledge_book_file(
    original_name: str,
    media_type: str,
    content: bytes,
) -> dict[str, str | int]:
    """Validate an upload and return metadata suitable for persistence.

    The returned mapping deliberately excludes the bytes themselves.  The
    repository stores the caller-provided bytes after this function succeeds.
    """

    validate_knowledge_book_file_name(original_name)
    normalized_media_type = (media_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    extension = _extension(original_name)
    basename = original_name.lower().rsplit("/", 1)[-1]
    is_generic_octet_stream = normalized_media_type == "application/octet-stream"
    is_trusted_text_name = extension in _SUPPORTED_EXTENSIONS or basename in _SUPPORTED_FILENAMES
    if (
        extension not in _SUPPORTED_EXTENSIONS
        and basename not in _SUPPORTED_FILENAMES
        and not (not extension and _is_text_media_type(normalized_media_type))
    ):
        raise ValueError(f"不支持的教材文件类型：{original_name}")
    if not isinstance(content, bytes) or not content:
        raise ValueError("教材文件不能为空")
    if len(content) > MAX_KNOWLEDGE_BOOK_FILE_BYTES:
        raise ValueError(f"教材文件不能超过 {MAX_KNOWLEDGE_BOOK_FILE_BYTES} 字节")

    if not _is_text_media_type(normalized_media_type) and not (
        is_generic_octet_stream and is_trusted_text_name
    ):
        raise ValueError(f"教材文件必须是可预览的文本类型：{normalized_media_type}")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("教材文件必须是可预览的文本类型") from error
    if b"\x00" in content:
        raise ValueError("教材文件必须是可预览的文本类型")
    if is_generic_octet_stream:
        normalized_media_type = "text/plain"

    return {
        "original_name": original_name,
        "display_name": original_name,
        "media_type": normalized_media_type,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def file_token(file_id: str) -> str:
    """Return the stable Markdown target used for an embedded file."""

    return f"book-file:{file_id}"


def extract_knowledge_book_file_ids(markdown: str) -> list[str]:
    """Extract unique embedded file IDs in first-appearance order."""

    seen: set[str] = set()
    result: list[str] = []
    for match in _FILE_TOKEN_RE.finditer(markdown or ""):
        file_id = match.group(1).lower()
        if file_id not in seen:
            seen.add(file_id)
            result.append(file_id)
    return result
