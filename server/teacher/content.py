"""Validation and normalization for teacher-authored knowledge book pages."""

from __future__ import annotations

import re
from pathlib import PurePath

from server.teacher.models import TeacherBookImportPreview


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_TAB_RE = re.compile(r"^\s*#@tab\s+(.+?)\s*$", re.IGNORECASE)
_UNSAFE_MARKUP_RE = re.compile(
    r"<\s*/?\s*[a-z][^>]*>|\bon(?:error|load|click)\s*=|\b(?:javascript|data):",
    re.IGNORECASE,
)
_EXTERNAL_RESOURCE_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*<?(?:https?:|//|data:|javascript:)",
    re.IGNORECASE,
)
_EXTERNAL_REFERENCE_RE = re.compile(
    r"^\s{0,3}\[[^\]]+\]:\s*<?(?:https?:|//|data:|javascript:)",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+")
MAX_MARKDOWN_BYTES = 1 * 1024 * 1024


def _is_valid_markdown_name(file_name: str) -> bool:
    path = PurePath(file_name)
    return (
        path.name == file_name
        and path.suffix.lower() == ".md"
        and file_name not in {".", ".."}
        and ".." not in path.parts
    )


def _tab_names(raw: str) -> set[str]:
    return {
        name.strip().lower()
        for name in re.split(r"[,\s]+", raw)
        if name.strip()
    }


def _filter_code_tabs(content: str) -> tuple[str, list[str]]:
    """Keep unmarked code and the PyTorch/all D2L code-tab segments.

    D2L places ``#@tab`` markers inside fenced code blocks.  Treating these
    markers as Markdown-wide directives would incorrectly delete ordinary
    prose, so the state machine deliberately only acts while a fence is open.
    """

    lines = content.splitlines(keepends=True)
    output: list[str] = []
    removed: set[str] = set()
    fence_char: str | None = None
    fence_length = 0
    active_tabs: set[str] | None = None
    fence_output_start = 0
    block_has_tabs = False
    block_has_allowed_tabs = False

    for line in lines:
        fence_match = _FENCE_RE.match(line)
        if fence_char is None:
            output.append(line)
            if fence_match:
                marker = fence_match.group(1)
                fence_char = marker[0]
                fence_length = len(marker)
                active_tabs = None
                fence_output_start = len(output) - 1
                block_has_tabs = False
                block_has_allowed_tabs = False
            continue

        tab_match = _TAB_RE.match(line)
        if tab_match:
            active_tabs = _tab_names(tab_match.group(1))
            block_has_tabs = True
            block_has_allowed_tabs = block_has_allowed_tabs or bool(active_tabs & {"pytorch", "all"})
            removed.update(active_tabs - {"pytorch", "all"})
            continue

        closing = (
            fence_match is not None
            and fence_match.group(1)[0] == fence_char
            and len(fence_match.group(1)) >= fence_length
        )
        if closing:
            if block_has_tabs and not block_has_allowed_tabs:
                del output[fence_output_start:]
            else:
                output.append(line)
            fence_char = None
            fence_length = 0
            active_tabs = None
            block_has_tabs = False
            block_has_allowed_tabs = False
        elif active_tabs is None or active_tabs & {"pytorch", "all"}:
            output.append(line)

    if fence_char is not None and block_has_tabs and not block_has_allowed_tabs:
        del output[fence_output_start:]

    return "".join(output), sorted(removed)


def _heading_warnings(content: str) -> list[str]:
    warnings: list[str] = []
    previous_level: int | None = None
    fence_char: str | None = None
    fence_length = 0
    for line_number, line in enumerate(content.splitlines(), start=1):
        fence_match = _FENCE_RE.match(line)
        if fence_char is not None:
            if fence_match and fence_match.group(1)[0] == fence_char and len(fence_match.group(1)) >= fence_length:
                fence_char = None
            continue
        if fence_match:
            marker = fence_match.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            continue
        heading = _HEADING_RE.match(line)
        if not heading:
            continue
        level = len(heading.group(1))
        if previous_level is not None and level > previous_level + 1:
            warnings.append(f"第 {line_number} 行标题层级从 h{previous_level} 跳到 h{level}，建议补齐中间层级")
        previous_level = level
    return warnings


def normalize_teacher_markdown(file_name: str, content_markdown: str) -> TeacherBookImportPreview:
    if not _is_valid_markdown_name(file_name):
        raise ValueError("教材导入只接受不含路径的 .md 文件")
    if _UNSAFE_MARKUP_RE.search(content_markdown):
        raise ValueError("教材 Markdown 不支持原始 HTML、脚本、嵌入或危险链接标记")
    if _EXTERNAL_RESOURCE_RE.search(content_markdown) or _EXTERNAL_REFERENCE_RE.search(content_markdown):
        raise ValueError("教材 Markdown 不支持外部链接或外部图片资源")
    if len(content_markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ValueError("教材 Markdown 单文件不能超过 1 MB")

    normalized, removed_frameworks = _filter_code_tabs(
        content_markdown.replace("\r\n", "\n").replace("\r", "\n")
    )
    warnings: list[str] = []
    if removed_frameworks:
        warnings.append("已移除非 PyTorch 代码标签，仅保留 PyTorch/all 代码片段")
    if not normalized.strip():
        warnings.append("正文为空，保存后不能发布")
    warnings.extend(_heading_warnings(normalized))
    return TeacherBookImportPreview(
        file_name=file_name,
        content_markdown=normalized,
        removed_frameworks=removed_frameworks,
        warnings=warnings,
    )
