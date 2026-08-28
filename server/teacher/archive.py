"""Safe parsing of teacher knowledge-book ZIP packages.

The parser is intentionally independent from the database.  Preview requests
can therefore inspect a package without creating a draft, while the service
decides which existing catalogue points the package is allowed to update.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

from server.teacher.content import normalize_teacher_markdown


MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_FILES = 200
MAX_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
MAX_MARKDOWN_BYTES = 1 * 1024 * 1024
MAX_ASSET_BYTES = 5 * 1024 * 1024
ALLOWED_ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\(\s*)(?:<([^>]+)>|(\S+?))(\s*(?:\"[^\"]*\"|'[^']*')?\s*\))")


@dataclass(frozen=True)
class ArchivePage:
    topic_id: str
    topic_name: str | None
    topic_sort_order: int
    knowledge_point_id: str
    file_name: str
    title: str | None
    sort_order: int
    content_markdown: str
    removed_frameworks: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class ArchiveAsset:
    path: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class ParsedTeacherBookArchive:
    title: str
    format_version: int
    pages: list[ArchivePage]
    assets: list[ArchiveAsset]


def _safe_zip_path(raw_name: str) -> str:
    name = raw_name.replace("\\", "/")
    if not name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
        raise ValueError("教材压缩包包含不安全路径")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("教材压缩包包含路径穿越文件")
    return "/".join(path.parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return info.create_system == 3 and stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _manifest_pages(manifest: object) -> tuple[str, int, list[dict[str, object]]]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 必须是 JSON 对象")
    if manifest.get("format_version") != 1:
        raise ValueError("manifest.json 的 format_version 必须为 1")
    title = manifest.get("title")
    topics = manifest.get("topics")
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise ValueError("manifest.json 缺少有效的教材 title")
    if not isinstance(topics, list) or not topics:
        raise ValueError("manifest.json 至少需要一个主题")

    pages: list[dict[str, str | None]] = []
    topic_ids: set[str] = set()
    point_ids: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("manifest.json 的 topics 项必须是对象")
        topic_id = topic.get("id")
        topic_name = topic.get("name")
        topic_sort_order = topic.get("sort_order", 0)
        points = topic.get("knowledge_points")
        if not isinstance(topic_id, str) or not topic_id.strip() or topic_id in topic_ids:
            raise ValueError("manifest.json 包含重复或无效的主题 ID")
        if not isinstance(points, list):
            raise ValueError("manifest.json 的 knowledge_points 必须是数组")
        if topic_name is not None and (not isinstance(topic_name, str) or len(topic_name) > 120):
            raise ValueError(f"主题 {topic_id} 的 name 无效")
        if not isinstance(topic_sort_order, int) or topic_sort_order < 0:
            raise ValueError(f"主题 {topic_id} 的 sort_order 无效")
        topic_ids.add(topic_id)
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("manifest.json 的知识点项必须是对象")
            point_id = point.get("id")
            file_name = point.get("file")
            point_title = point.get("name")
            sort_order = point.get("sort_order", 0)
            if not isinstance(point_id, str) or not point_id.strip() or point_id in point_ids:
                raise ValueError("manifest.json 包含重复或无效的知识点 ID")
            if not isinstance(file_name, str) or not file_name.lower().endswith(".md"):
                raise ValueError(f"知识点 {point_id} 必须指向 .md 文件")
            if point_title is not None and (not isinstance(point_title, str) or len(point_title) > 120):
                raise ValueError(f"知识点 {point_id} 的 name 无效")
            if not isinstance(sort_order, int) or sort_order < 0:
                raise ValueError(f"知识点 {point_id} 的 sort_order 无效")
            point_ids.add(point_id)
            pages.append({
                "topic_id": topic_id,
                "topic_name": topic_name,
                "topic_sort_order": topic_sort_order,
                "knowledge_point_id": point_id,
                "file": file_name,
                "title": point_title,
                "sort_order": sort_order,
            })
    return title.strip(), 1, pages


def _asset_namespace(knowledge_point_id: str) -> str:
    return hashlib.sha256(knowledge_point_id.encode("utf-8")).hexdigest()[:24]


def _scoped_asset_path(knowledge_point_id: str, source_path: str) -> str:
    return f"assets/pages/{_asset_namespace(knowledge_point_id)}/{source_path.removeprefix('assets/')}"


def _rewrite_local_images(
    content: str,
    *,
    file_name: str,
    files: set[str],
    workspace_id: str,
    knowledge_point_id: str,
) -> tuple[str, list[str], set[str]]:
    parent = posixpath.dirname(file_name)
    warnings: list[str] = []
    referenced_assets: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        raw_url = match.group(2) or match.group(3) or ""
        if raw_url.startswith(("/", "#", "http:", "https:", "//", "data:", "javascript:")):
            return match.group(0)
        asset_path = posixpath.normpath(posixpath.join(parent, raw_url))
        if asset_path not in files and raw_url.startswith("assets/") and raw_url in files:
            asset_path = raw_url
        if asset_path not in files and not raw_url.startswith("../") and f"assets/{raw_url}" in files:
            asset_path = f"assets/{raw_url}"
        if not asset_path.startswith("assets/") or asset_path not in files:
            raise ValueError(f"教材图片资源不存在或不在 assets/ 目录：{raw_url}")
        referenced_assets.add(asset_path)
        stored_asset_path = _scoped_asset_path(knowledge_point_id, asset_path)
        api_url = f"/api/v1/learning/book/{quote(workspace_id, safe='')}/assets/{quote(stored_asset_path, safe='/')}"
        return f"{match.group(1)}{api_url}{match.group(4)}"

    return _IMAGE_RE.sub(replace, content), warnings, referenced_assets


def _read_zip_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, label: str) -> bytes:
    try:
        return archive.read(info)
    except (OSError, RuntimeError, EOFError, zlib.error, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError(f"{label}无法读取") from error


def parse_teacher_book_archive(file_name: str, archive_bytes: bytes, *, workspace_id: str) -> ParsedTeacherBookArchive:
    if not file_name.lower().endswith(".zip"):
        raise ValueError("批量教材导入只接受 .zip 文件")
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("教材压缩包不能超过 10 MB")
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(archive_bytes))
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("教材压缩包无法读取") from error

    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        raise ValueError(f"教材压缩包文件数不能超过 {MAX_FILES}")
    names: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in infos:
        if info.is_dir():
            continue
        if _is_symlink(info):
            raise ValueError("教材压缩包不允许包含符号链接")
        name = _safe_zip_path(info.filename)
        if name in names:
            raise ValueError(f"教材压缩包包含重复路径：{name}")
        if info.file_size < 0:
            raise ValueError("教材压缩包包含无效文件大小")
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("教材压缩包解压后不能超过 30 MB")
        names[name] = info

    if "manifest.json" not in names:
        raise ValueError("教材压缩包根目录必须包含 manifest.json")
    try:
        manifest = json.loads(
            _read_zip_entry(archive, names["manifest.json"], label="manifest.json ").decode("utf-8-sig")
        )
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise ValueError("manifest.json 必须是 UTF-8 JSON") from error
    title, format_version, manifest_pages = _manifest_pages(manifest)

    contents: dict[str, bytes] = {}
    source_assets: dict[str, ArchiveAsset] = {}
    for name, info in names.items():
        if name == "manifest.json":
            continue
        suffix = PurePosixPath(name).suffix.lower()
        if name.startswith("assets/"):
            if suffix not in ALLOWED_ASSET_TYPES:
                raise ValueError(f"教材压缩包包含不支持的文件：{name}")
            if info.file_size > MAX_ASSET_BYTES:
                raise ValueError(f"图片资源不能超过 5 MB：{name}")
            source_assets[name] = ArchiveAsset(
                name,
                ALLOWED_ASSET_TYPES[suffix],
                _read_zip_entry(archive, info, label=f"图片资源 {name} "),
            )
            continue
        if suffix == ".md":
            if info.file_size > MAX_MARKDOWN_BYTES:
                raise ValueError(f"Markdown 文件不能超过 1 MB：{name}")
            contents[name] = _read_zip_entry(archive, info, label=f"Markdown 文件 {name} ")
            continue
        raise ValueError(f"教材压缩包包含不支持的文件：{name}")

    pages: list[ArchivePage] = []
    page_assets: dict[tuple[str, str], ArchiveAsset] = {}
    for entry in manifest_pages:
        file_name = _safe_zip_path(str(entry["file"]))
        raw = contents.get(file_name)
        if raw is None:
            raise ValueError(f"manifest.json 引用的 Markdown 不存在：{file_name}")
        try:
            markdown = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(f"Markdown 必须使用 UTF-8 编码：{file_name}") from error
        normalized = normalize_teacher_markdown(PurePosixPath(file_name).name, markdown)
        rewritten, image_warnings, referenced_assets = _rewrite_local_images(
            normalized.content_markdown,
            file_name=file_name,
            files=set(names),
            workspace_id=workspace_id,
            knowledge_point_id=str(entry["knowledge_point_id"]),
        )
        for source_path in referenced_assets:
            source_asset = source_assets[source_path]
            stored_path = _scoped_asset_path(str(entry["knowledge_point_id"]), source_path)
            page_assets[(str(entry["knowledge_point_id"]), source_path)] = ArchiveAsset(
                stored_path,
                source_asset.media_type,
                source_asset.content,
            )
        pages.append(
            ArchivePage(
                topic_id=str(entry["topic_id"]),
                topic_name=str(entry["topic_name"]) if entry["topic_name"] is not None else None,
                topic_sort_order=int(entry["topic_sort_order"]),
                knowledge_point_id=str(entry["knowledge_point_id"]),
                file_name=file_name,
                title=str(entry["title"]) if entry["title"] is not None else None,
                sort_order=int(entry["sort_order"]),
                content_markdown=rewritten,
                removed_frameworks=normalized.removed_frameworks,
                warnings=[*normalized.warnings, *image_warnings],
            )
        )
    return ParsedTeacherBookArchive(
        title=title,
        format_version=format_version,
        pages=pages,
        assets=list(page_assets.values()),
    )
