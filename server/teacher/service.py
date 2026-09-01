from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import posixpath
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from configs.settings import settings
from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service
from server.teacher.archive import (
    ALLOWED_ASSET_TYPES,
    MAX_ASSET_BYTES,
    _rewrite_local_images,
    _safe_zip_path,
    _scoped_asset_path,
)
from server.teacher.ai_analysis import (
    build_ai_analysis_material,
    generate_ai_analysis,
    learning_analysis_ai_cache,
)
from server.teacher.analytics import build_analytics, build_learning_analysis, build_monthly_analytics, filter_period_rows
from server.teacher.models import (
    ExerciseBlueprint,
    GuidedBlueprint,
    LearningBookNavigationItem,
    LearningBookPage,
    ReviewBlueprint,
    TeacherBookImportApplyRequest,
    TeacherBookAssetInput,
    TeacherBookImportPreview,
    TeacherBookImportPreviewRequest,
    TeacherBookArchiveImportApplyRequest,
    TeacherBookArchiveImportPreview,
    TeacherBookArchiveImportPreviewRequest,
    TeacherBookArchiveItemPreview,
    TeacherBookNavigationItem,
    TeacherBookPage,
    TeacherCatalog,
    TeacherAnalysisAnnotations,
    TeacherAIAnalysisRequest,
    TeachingGoals,
    UpdateTeacherAnalysisAnnotations,
    UpdateTeacherBookPage,
    UpdateTeacherCatalog,
    UpdateTeachingGoals,
    PublishTeacherBookPage,
)
from server.teacher.content import normalize_teacher_markdown
from server.teacher.archive import parse_teacher_book_archive


class TeacherService:
    @staticmethod
    def require_teacher(
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        permission: Permission = Permission.LEARNING_CONTENT_MANAGE,
    ) -> None:
        authorization_service.require(
            principal,
            permission,
            workspace_id=workspace_id,
        )

    async def goals(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        settings = await gateway.get_user_settings(principal)
        key = f"teacher_goals:{workspace_id}"
        value = settings["settings"].get(key) or TeachingGoals(workspace_id=workspace_id).model_dump(mode="json")
        return {"goals": value, "revision": settings["revision"], "updated_at": settings["updated_at"]}

    async def update_goals(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, body: UpdateTeachingGoals) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        goals = TeachingGoals(workspace_id=workspace_id, **body.model_dump()).model_dump(mode="json")
        result = await gateway.update_user_settings(principal, {f"teacher_goals:{workspace_id}": goals})
        return {"goals": goals, "revision": result["revision"], "updated_at": result["updated_at"]}

    @staticmethod
    def _default_annotations(workspace_id: str) -> dict[str, Any]:
        return TeacherAnalysisAnnotations(workspace_id=workspace_id).model_dump(mode="json")

    async def analysis_annotations(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        settings = await gateway.get_user_settings(principal)
        key = f"teacher_analysis_annotations:{workspace_id}"
        value = settings["settings"].get(key) or self._default_annotations(workspace_id)
        return {"annotations": value, "revision": settings["revision"], "updated_at": settings["updated_at"]}

    async def update_analysis_annotations(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, body: UpdateTeacherAnalysisAnnotations) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        annotations = TeacherAnalysisAnnotations(workspace_id=workspace_id, **body.model_dump()).model_dump(mode="json")
        result = await gateway.update_user_settings(principal, {f"teacher_analysis_annotations:{workspace_id}": annotations})
        return {"annotations": annotations, "revision": result["revision"], "updated_at": result["updated_at"]}

    async def catalog(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str) -> dict[str, Any]:
        self.require_teacher(
            principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM
        )
        result = await gateway.get_teaching_catalog(principal, workspace_id)
        value = TeacherCatalog.model_validate(result["catalog"])
        return {"catalog": value.model_dump(mode="json"), "revision": result["revision"], "updated_at": result["updated_at"]}

    async def update_catalog(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, body: UpdateTeacherCatalog) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        value = TeacherCatalog(workspace_id=workspace_id, **body.model_dump())
        self._validate_blueprint_links(value)
        catalog = value.model_dump(mode="json")
        result = await gateway.update_teaching_catalog(principal, workspace_id, catalog)
        return {"catalog": catalog, "revision": result["revision"], "updated_at": result["updated_at"]}

    @staticmethod
    def _catalog_point(catalog: TeacherCatalog, knowledge_point_id: str) -> tuple[Any, Any]:
        for topic in catalog.topics:
            for point in topic.knowledge_points:
                if point.id == knowledge_point_id:
                    return topic, point
        raise FileNotFoundError(knowledge_point_id)

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else value

    @classmethod
    def _teacher_book_page(
        cls,
        workspace_id: str,
        topic: Any,
        point: Any,
        row: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return TeacherBookPage(
            workspace_id=workspace_id,
            topic_id=topic.id,
            topic_name=topic.name,
            knowledge_point_id=point.id,
            title=point.name,
            draft_markdown=str(row.get("draft_markdown", "")) if row else "",
            published_markdown=row.get("published_markdown") if row else None,
            revision=int(row.get("revision", 0)) if row else 0,
            published_revision=(
                int(row["published_revision"])
                if row and row.get("published_revision") is not None
                else None
            ),
            updated_at=cls._timestamp(row.get("updated_at")) if row else None,
        ).model_dump(mode="json")

    async def teacher_book_navigation(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        rows = await gateway.list_knowledge_pages(principal, workspace_id)
        pages = {str(row["knowledge_point_id"]): row for row in rows}
        items: list[dict[str, Any]] = []
        for topic in catalog.topics:
            for point in topic.knowledge_points:
                row = pages.get(point.id)
                item = TeacherBookNavigationItem(
                    topic_id=topic.id,
                    topic_name=topic.name,
                    knowledge_point_id=point.id,
                    title=point.name,
                    sort_order=point.sort_order,
                    topic_status=topic.status,
                    knowledge_point_status=point.status,
                    has_draft=bool(row and str(row.get("draft_markdown", "")).strip()),
                    has_published=bool(row and row.get("published_markdown") is not None),
                    revision=int(row.get("revision", 0)) if row else 0,
                    published_revision=(
                        int(row["published_revision"])
                        if row and row.get("published_revision") is not None
                        else None
                    ),
                )
                items.append(item.model_dump(mode="json"))
        return {"workspace_id": workspace_id, "items": items}

    async def teacher_book_page(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, knowledge_point_id: str) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        topic, point = self._catalog_point(catalog, knowledge_point_id)
        row = await gateway.get_knowledge_page(principal, workspace_id, knowledge_point_id)
        return {"page": self._teacher_book_page(workspace_id, topic, point, row)}

    async def learning_book_navigation(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str) -> dict[str, Any]:
        authorization_service.require(
            principal, Permission.LEARNING_CONTENT_READ_WORKSPACE, workspace_id=workspace_id
        )
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        rows = await gateway.list_knowledge_pages(principal, workspace_id)
        pages = {str(row["knowledge_point_id"]): row for row in rows}
        items: list[dict[str, Any]] = []
        for topic in catalog.topics:
            if topic.status != "enabled":
                continue
            for point in topic.knowledge_points:
                row = pages.get(point.id)
                if point.status != "enabled" or not row or row.get("published_markdown") is None:
                    continue
                items.append(
                    LearningBookNavigationItem(
                        topic_id=topic.id,
                        topic_name=topic.name,
                        knowledge_point_id=point.id,
                        title=point.name,
                        sort_order=point.sort_order,
                        revision=int(row["published_revision"]),
                    ).model_dump(mode="json")
                )
        return {"workspace_id": workspace_id, "items": items}

    async def learning_book_page(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, knowledge_point_id: str) -> dict[str, Any]:
        authorization_service.require(
            principal, Permission.LEARNING_CONTENT_READ_WORKSPACE, workspace_id=workspace_id
        )
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        topic, point = self._catalog_point(catalog, knowledge_point_id)
        if topic.status != "enabled" or point.status != "enabled":
            raise FileNotFoundError(knowledge_point_id)
        row = await gateway.get_published_knowledge_page(principal, workspace_id, knowledge_point_id)
        if row is None:
            raise FileNotFoundError(knowledge_point_id)
        page = LearningBookPage(
            workspace_id=workspace_id,
            topic_id=topic.id,
            topic_name=topic.name,
            knowledge_point_id=point.id,
            title=point.name,
            content_markdown=str(row["published_markdown"]),
            revision=int(row["published_revision"] or row["revision"]),
        )
        return {"page": page.model_dump(mode="json")}

    async def knowledge_book_asset(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        asset_path: str,
    ) -> dict[str, Any]:
        if "\\" in asset_path or "\x00" in asset_path or not asset_path.startswith("assets/"):
            raise FileNotFoundError(asset_path)
        parts = asset_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise FileNotFoundError(asset_path)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        visible_point_ids = {
            point.id
            for topic in catalog.topics
            if topic.status == "enabled"
            for point in topic.knowledge_points
            if point.status == "enabled"
        }
        asset_marker = f"/assets/{quote(asset_path, safe='/')}"
        rows = await gateway.list_knowledge_pages(principal, workspace_id)
        if not any(
            str(row.get("knowledge_point_id")) in visible_point_ids
            and row.get("published_markdown") is not None
            and asset_marker in str(row["published_markdown"])
            for row in rows
        ):
            raise FileNotFoundError(asset_path)
        asset = await gateway.get_knowledge_book_asset(principal, workspace_id, asset_path)
        if asset is None:
            raise FileNotFoundError(asset_path)
        return asset

    @staticmethod
    def _prepare_teacher_book_content(
        workspace_id: str,
        knowledge_point_id: str,
        file_name: str,
        content_markdown: str,
        asset_inputs: list[TeacherBookAssetInput],
    ) -> tuple[TeacherBookImportPreview, str, list[dict[str, Any]]]:
        normalized = normalize_teacher_markdown(file_name, content_markdown)
        uploaded_assets: dict[str, tuple[str, bytes]] = {}
        for item in asset_inputs:
            asset_path = _safe_zip_path(item.asset_path)
            if not asset_path.startswith("assets/"):
                raise ValueError("单篇 Markdown 的图片资源必须位于 assets/ 目录")
            suffix = posixpath.splitext(asset_path)[1].lower()
            expected_media_type = ALLOWED_ASSET_TYPES.get(suffix)
            if expected_media_type is None or item.media_type != expected_media_type:
                raise ValueError(f"不支持的教材图片资源：{asset_path}")
            if asset_path in uploaded_assets:
                raise ValueError(f"教材图片资源路径重复：{asset_path}")
            try:
                content = base64.b64decode(item.content_base64, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError(f"教材图片资源编码无效：{asset_path}") from error
            if not content or len(content) > MAX_ASSET_BYTES:
                raise ValueError(f"图片资源不能超过 5 MB：{asset_path}")
            uploaded_assets[asset_path] = (item.media_type, content)

        rewritten, _image_warnings, referenced_assets = _rewrite_local_images(
            normalized.content_markdown,
            file_name="page.md",
            files=set(uploaded_assets),
            workspace_id=workspace_id,
            knowledge_point_id=knowledge_point_id,
        )
        assets = [
            {
                "asset_path": _scoped_asset_path(knowledge_point_id, source_path),
                "media_type": uploaded_assets[source_path][0],
                "content": uploaded_assets[source_path][1],
                "sha256": hashlib.sha256(uploaded_assets[source_path][1]).hexdigest(),
            }
            for source_path in referenced_assets
        ]
        return normalized, rewritten, assets

    async def update_teacher_book_page(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, knowledge_point_id: str, body: UpdateTeacherBookPage) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        topic, point = self._catalog_point(catalog, knowledge_point_id)
        normalized, rewritten, assets = self._prepare_teacher_book_content(
            workspace_id,
            knowledge_point_id,
            "page.md",
            body.content_markdown,
            body.assets,
        )
        rows = await gateway.apply_knowledge_book_import(
            principal,
            workspace_id,
            [{
                "knowledge_point_id": knowledge_point_id,
                "expected_revision": body.expected_revision,
                "content_markdown": rewritten,
            }],
            assets,
        )
        return {
            "page": self._teacher_book_page(workspace_id, topic, point, rows[0]),
            "warnings": normalized.warnings,
        }

    async def publish_teacher_book_page(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, knowledge_point_id: str, body: PublishTeacherBookPage) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        topic, point = self._catalog_point(catalog, knowledge_point_id)
        row = await gateway.publish_knowledge_page(
            principal, workspace_id, knowledge_point_id, expected_revision=body.expected_revision
        )
        return {"page": self._teacher_book_page(workspace_id, topic, point, row)}

    async def preview_teacher_book_import(self, principal: AuthenticatedPrincipal, workspace_id: str, body: TeacherBookImportPreviewRequest) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        return normalize_teacher_markdown(body.file_name, body.content_markdown).model_dump(mode="json")

    async def apply_teacher_book_import(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, body: TeacherBookImportApplyRequest) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        topic, point = self._catalog_point(catalog, body.knowledge_point_id)
        normalized, rewritten, assets = self._prepare_teacher_book_content(
            workspace_id,
            body.knowledge_point_id,
            body.file_name,
            body.content_markdown,
            body.assets,
        )
        rows = await gateway.apply_knowledge_book_import(
            principal,
            workspace_id,
            [{
                "knowledge_point_id": body.knowledge_point_id,
                "expected_revision": body.expected_revision,
                "content_markdown": rewritten,
            }],
            assets,
        )
        row = rows[0]
        return {
            "page": self._teacher_book_page(workspace_id, topic, point, row),
            "warnings": normalized.warnings,
        }

    @staticmethod
    def _decode_archive(value: str) -> bytes:
        try:
            archive = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("教材压缩包编码无效") from error
        if not archive:
            raise ValueError("教材压缩包不能为空")
        return archive

    async def _teacher_book_archive_preview(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        file_name: str,
        archive_base64: str,
    ) -> tuple[TeacherBookArchiveImportPreview, Any, Any]:
        self.require_teacher(principal, workspace_id)
        parsed = parse_teacher_book_archive(
            file_name,
            self._decode_archive(archive_base64),
            workspace_id=workspace_id,
        )
        catalog = TeacherCatalog.model_validate(
            (await gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        rows = await gateway.list_knowledge_pages(principal, workspace_id)
        row_by_point = {str(row["knowledge_point_id"]): row for row in rows}
        catalog_points = {
            point.id: (topic, point)
            for topic in catalog.topics
            for point in topic.knowledge_points
        }
        items: list[TeacherBookArchiveItemPreview] = []
        imported_ids: set[str] = set()
        for archive_page in parsed.pages:
            target = catalog_points.get(archive_page.knowledge_point_id)
            if target is None:
                raise ValueError(f"Manifest 知识点不存在于当前教师目录：{archive_page.knowledge_point_id}")
            topic, point = target
            if topic.id != archive_page.topic_id:
                raise ValueError(
                    f"知识点 {archive_page.knowledge_point_id} 的主题 ID 与教师目录不一致"
                )
            row = row_by_point.get(point.id)
            current_content = str(row.get("draft_markdown", "")) if row else ""
            expected_revision = int(row.get("revision", 0)) if row else 0
            action = "create" if row is None else (
                "unchanged" if current_content == archive_page.content_markdown else "update"
            )
            warnings = list(archive_page.warnings)
            if archive_page.topic_name and archive_page.topic_name != topic.name:
                warnings.append("Manifest 中的主题名称与教师目录不同，已以教师目录名称为准")
            if archive_page.title and archive_page.title != point.name:
                warnings.append("Manifest 中的知识点名称与教师目录不同，已以教师目录名称为准")
            items.append(
                TeacherBookArchiveItemPreview(
                    topic_id=topic.id,
                    knowledge_point_id=point.id,
                    title=point.name,
                    file_name=archive_page.file_name,
                    action=action,
                    expected_revision=expected_revision,
                    current_markdown=current_content,
                    content_markdown=archive_page.content_markdown,
                    removed_frameworks=archive_page.removed_frameworks,
                    warnings=warnings,
                )
            )
            imported_ids.add(point.id)

        omitted = [
            point.id
            for topic in catalog.topics
            for point in topic.knowledge_points
            if point.id not in imported_ids
        ]
        warnings = []
        if omitted:
            warnings.append(f"本次包未更新 {len(omitted)} 个目录知识点，现有草稿不会被删除")
        preview = TeacherBookArchiveImportPreview(
            file_name=file_name,
            format_version=parsed.format_version,
            title=parsed.title,
            items=items,
            asset_paths=[asset.path for asset in parsed.assets],
            omitted_knowledge_points=omitted,
            warnings=warnings,
        )
        return preview, parsed, catalog

    async def preview_teacher_book_archive_import(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        body: TeacherBookArchiveImportPreviewRequest,
    ) -> dict[str, Any]:
        preview, _parsed, _catalog = await self._teacher_book_archive_preview(
            principal, gateway, workspace_id, body.file_name, body.archive_base64
        )
        return preview.model_dump(mode="json")

    async def apply_teacher_book_archive_import(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        body: TeacherBookArchiveImportApplyRequest,
    ) -> dict[str, Any]:
        preview, parsed, catalog = await self._teacher_book_archive_preview(
            principal, gateway, workspace_id, body.file_name, body.archive_base64
        )
        expected_by_point = body.expected_revisions
        missing_revisions = [
            item.knowledge_point_id
            for item in preview.items
            if item.action != "unchanged" and item.knowledge_point_id not in expected_by_point
        ]
        if missing_revisions:
            raise ValueError(f"教材批量导入缺少版本号：{', '.join(missing_revisions)}")
        pages = [
            {
                "knowledge_point_id": item.knowledge_point_id,
                "expected_revision": int(expected_by_point[item.knowledge_point_id]),
                "content_markdown": item.content_markdown,
            }
            for item in preview.items
            if item.action != "unchanged"
        ]
        assets = [
            {
                "asset_path": asset.path,
                "media_type": asset.media_type,
                "content": asset.content,
                "sha256": hashlib.sha256(asset.content).hexdigest(),
            }
            for asset in parsed.assets
        ]
        rows = await gateway.apply_knowledge_book_import(principal, workspace_id, pages, assets)
        points = {
            point.id: (topic, point)
            for topic in catalog.topics
            for point in topic.knowledge_points
        }
        return {
            "pages": [
                self._teacher_book_page(workspace_id, points[str(row["knowledge_point_id"])][0], points[str(row["knowledge_point_id"])][1], row)
                for row in rows
                if row is not None
            ],
            "asset_paths": [asset.path for asset in parsed.assets],
            "applied_count": len(rows),
        }

    @staticmethod
    def _validate_blueprint_links(catalog: TeacherCatalog) -> None:
        points = {
            (topic.id, point.id): point
            for topic in catalog.topics
            for point in topic.knowledge_points
        }
        exercise_ids = {blueprint.id: blueprint for blueprint in catalog.exercise_blueprints}
        for blueprint in [*catalog.exercise_blueprints, *catalog.review_blueprints, *catalog.guided_blueprints]:
            point = points.get((blueprint.topic_id, blueprint.knowledge_point_id))
            if point is None:
                raise ValueError("蓝图必须关联其所属主题中的一个知识点")
            if blueprint.status == "enabled" and not isinstance(blueprint, GuidedBlueprint):
                if blueprint.question_type not in point.question_types:
                    raise ValueError("蓝图题型必须是其知识点已启用的题型")
                if not blueprint.rubric:
                    raise ValueError("启用蓝图前必须至少配置一个评分标准")
                for point in blueprint.rubric:
                    criterion = str(point.get("criterion", "")).strip()
                    try:
                        weight = float(point.get("weight", 0))
                    except (TypeError, ValueError):
                        weight = 0
                    if not criterion or weight <= 0:
                        raise ValueError("启用蓝图的每个评分标准都必须填写内容并设置正权重")
        for blueprint in catalog.review_blueprints:
            if blueprint.exercise_blueprint_id is None:
                continue
            target = exercise_ids.get(blueprint.exercise_blueprint_id)
            if target is None or (target.topic_id, target.knowledge_point_id) != (blueprint.topic_id, blueprint.knowledge_point_id):
                raise ValueError("复习蓝图关联的练习蓝图必须属于同一主题和知识点")

    async def upsert_exercise_blueprint(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, blueprint: ExerciseBlueprint) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        current = await gateway.get_teaching_catalog(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(current["catalog"])
        items = [item for item in catalog.exercise_blueprints if item.id != blueprint.id]
        updated = UpdateTeacherCatalog(
            topics=catalog.topics, exercise_blueprints=[*items, blueprint], review_blueprints=catalog.review_blueprints, guided_blueprints=catalog.guided_blueprints,
        )
        return await self.update_catalog(principal, gateway, workspace_id, updated)

    async def upsert_review_blueprint(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, blueprint: ReviewBlueprint) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        current = await gateway.get_teaching_catalog(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(current["catalog"])
        items = [item for item in catalog.review_blueprints if item.id != blueprint.id]
        updated = UpdateTeacherCatalog(
            topics=catalog.topics, exercise_blueprints=catalog.exercise_blueprints, review_blueprints=[*items, blueprint], guided_blueprints=catalog.guided_blueprints,
        )
        return await self.update_catalog(principal, gateway, workspace_id, updated)

    async def upsert_guided_blueprint(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, blueprint: GuidedBlueprint) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        current = await gateway.get_teaching_catalog(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(current["catalog"])
        items = [item for item in catalog.guided_blueprints if item.id != blueprint.id]
        updated = UpdateTeacherCatalog(
            topics=catalog.topics, exercise_blueprints=catalog.exercise_blueprints,
            review_blueprints=catalog.review_blueprints, guided_blueprints=[*items, blueprint],
        )
        return await self.update_catalog(principal, gateway, workspace_id, updated)

    async def delete_blueprint(self, principal: AuthenticatedPrincipal, gateway: Any, workspace_id: str, blueprint_id: str, *, kind: str) -> dict[str, Any]:
        self.require_teacher(principal, workspace_id)
        current = await gateway.get_teaching_catalog(principal, workspace_id)
        catalog = TeacherCatalog.model_validate(current["catalog"])
        body = UpdateTeacherCatalog(
            topics=catalog.topics,
            exercise_blueprints=[item for item in catalog.exercise_blueprints if item.id != blueprint_id] if kind == "exercise" else catalog.exercise_blueprints,
            review_blueprints=[item for item in catalog.review_blueprints if item.id != blueprint_id] if kind == "review" else catalog.review_blueprints,
            guided_blueprints=[item for item in catalog.guided_blueprints if item.id != blueprint_id] if kind == "guided" else catalog.guided_blueprints,
        )
        return await self.update_catalog(principal, gateway, workspace_id, body)

    async def analytics(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        days: int = 30,
        *,
        period_start=None,
        period_end=None,
    ) -> dict[str, Any]:
        self.require_teacher(
            principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM
        )
        period_days = max(1, days)
        period_end = period_end or datetime.now(timezone.utc).date()
        period_start = period_start or period_end - timedelta(days=period_days - 1)
        period_days = max(1, (period_end - period_start).days + 1)
        monthly_start = period_end.replace(day=1)
        for _ in range(4):
            monthly_start = (monthly_start - timedelta(days=1)).replace(day=1)
        previous_start = period_start - timedelta(days=period_days)
        analytics_start = min(period_start, previous_start, monthly_start)
        monthly_since = datetime.combine(analytics_start, datetime.min.time(), tzinfo=timezone.utc).isoformat()
        since = datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc).isoformat()
        catalog = (await asyncio.to_thread(gateway.repository.get_teaching_catalog, workspace_id))["catalog"]
        student_user_ids = await asyncio.to_thread(gateway.repository.list_student_user_ids)
        monthly_question_rows = await asyncio.to_thread(gateway.repository.list_question_turns, workspace_id=workspace_id, since=monthly_since, timezone_name=settings.NLP_AGENT_ANALYTICS_TIMEZONE)
        question_rows = [
            row for row in monthly_question_rows
            if row.get("day") and period_start.isoformat() <= str(row["day"])[:10] <= period_end.isoformat()
        ]
        guided_rows = await asyncio.to_thread(gateway.repository.guided_session_stats, workspace_id=workspace_id, since=since)
        analysis_until = datetime.combine(period_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).isoformat()
        # One broad evidence/criterion fetch feeds both the overview (sliced to
        # the period just below) and the learning-analysis diagnostics (which
        # also need the preceding window and month-trend history).  This halves
        # the read-model queries for the page.  Each returns (rows, truncated)
        # so a silently dropped tail of old rows can be surfaced.
        analysis_evidence_rows, evidence_truncated = await asyncio.to_thread(
            gateway.repository.exercise_evidence_stats,
            workspace_id=workspace_id,
            since=monthly_since,
            until=analysis_until,
        )
        analysis_criterion_rows, criterion_truncated = await asyncio.to_thread(
            gateway.repository.exercise_criterion_stats,
            workspace_id=workspace_id,
            since=monthly_since,
            until=analysis_until,
        )
        evidence_rows = filter_period_rows(analysis_evidence_rows, period_start, period_end)
        criterion_rows = filter_period_rows(analysis_criterion_rows, period_start, period_end)
        result = build_analytics(
            question_rows, evidence_rows, criterion_rows, guided_rows, catalog,
            period_days=period_days,
            period_start=period_start,
            period_end=period_end,
            student_user_ids=student_user_ids,
        )
        monthly_statistics = build_monthly_analytics(
            monthly_question_rows,
            catalog,
            period_end=period_end,
            student_user_ids=student_user_ids,
        )
        learning_analysis = build_learning_analysis(
            question_rows,
            analysis_evidence_rows,
            analysis_criterion_rows,
            catalog,
            period_start=period_start,
            period_end=period_end,
            student_user_ids=student_user_ids,
        )
        return {
            "workspace_id": workspace_id,
            "period_days": period_days,
            "monthly_statistics": monthly_statistics,
            "learning_analysis": learning_analysis,
            "truncated": evidence_truncated or criterion_truncated,
            "data_completeness": {
                "complete": not (evidence_truncated or criterion_truncated),
                "evidence_truncated": evidence_truncated,
                "criterion_truncated": criterion_truncated,
                "message": (
                    "统计达到数据读取上限，更早的历史记录未能全部纳入分析；"
                    "当前周期的数据完整，但上期对比与历史趋势可能不完整。"
                    if (evidence_truncated or criterion_truncated)
                    else None
                ),
            },
            **result,
        }

    async def ai_analysis(
        self,
        principal: AuthenticatedPrincipal,
        gateway: Any,
        workspace_id: str,
        body: TeacherAIAnalysisRequest,
    ) -> dict[str, Any]:
        """Generate a cached, evidence-bound DeepSeek report on demand."""
        self.require_teacher(principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        end = body.end_date or datetime.now(timezone.utc).date()
        requested_days = max(1, body.period_days or 30)
        start = body.start_date or end - timedelta(days=requested_days - 1)
        overview = await self.analytics(
            principal,
            gateway,
            workspace_id,
            max(1, (end - start).days + 1),
            period_start=start,
            period_end=end,
        )
        material = build_ai_analysis_material(
            overview,
            course_id=body.course_id,
            content_scope=body.content_scope,
        )
        data_version = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        cache_key = ":".join(
            [
                workspace_id,
                body.course_id,
                body.content_scope,
                start.isoformat(),
                end.isoformat(),
                data_version,
            ]
        )
        if not body.force_refresh:
            cached = learning_analysis_ai_cache.get(cache_key)
            if cached is not None:
                cached["cache_hit"] = True
                return cached

        generated = await generate_ai_analysis(material)
        response = {
            **generated,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "DeepSeek",
            "model_id": generated.get("model_id"),
            "cache_hit": False,
            "scope": overview["learning_analysis"]["scope"],
            "course_id": body.course_id,
            "content_scope": body.content_scope,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "data_version": data_version,
        }
        if generated.get("status") == "completed":
            learning_analysis_ai_cache.set(cache_key, response)
        return response


teacher_service = TeacherService()
