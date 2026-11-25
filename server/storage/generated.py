"""Publish only completed FUSE staging records; never replace a user file."""
from __future__ import annotations

from sqlalchemy import select
from datetime import UTC, datetime, timedelta

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import UserFileModel
from server.storage.service import StorageService, StorageValidationError, _safe_name


async def completed_generation(service, generation_key):
    """Lookup before reserving bytes/items, so a retry works at full quota."""
    previous = await service.db.scalar(select(UserFileModel).where(
        UserFileModel.owner_user_id == service.scope.owner_user_id,
        UserFileModel.workspace_id == service.scope.workspace_id,
        UserFileModel.upload_key.startswith(generation_key + ':', autoescape=True),
    ).with_for_update())
    if previous is not None and (previous.status != 'active' or previous.origin != 'generated'
                                or previous.sha256 != previous.upload_key.rsplit(':', 1)[-1]):
        raise StorageValidationError('之前的生成结果已修改或删除，请使用新的文件名生成')
    return previous


async def cleanup_staging_file(factory, owner, workspace, file_id):
    # Trusted cleanup of a recorded task-owned ID, even after the login expired.
    # Do not accept a filename or remove anything outside generation_pending.
    async with factory.begin() as db:
        service = StorageService(db, AuthenticatedPrincipal(user_id=owner, workspace_ids={workspace}), workspace)
        await service.quota._lock_global_pool()
        await service.quota._locked_account()
        item = await db.scalar(select(UserFileModel).where(
            UserFileModel.id == file_id, UserFileModel.owner_user_id == owner,
            UserFileModel.workspace_id == workspace, UserFileModel.origin == 'generation_pending',
            UserFileModel.status == 'active').with_for_update())
        if item is not None:
            await service.delete(item.id)
            await service.permanently_delete(item.id)


async def purge_expired_staging_files(factory, *, owner=None, workspace=None):
    """Bounded recovery of abandoned generation tasks, including Manager restarts."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    async with factory() as db:
        query = select(UserFileModel.id, UserFileModel.owner_user_id, UserFileModel.workspace_id).where(
            UserFileModel.origin == 'generation_pending', UserFileModel.status == 'active',
            UserFileModel.created_at < cutoff)
        if owner is not None:
            query = query.where(UserFileModel.owner_user_id == owner, UserFileModel.workspace_id == workspace)
        rows = (await db.execute(query.limit(20))).all()
    for identifier, row_owner, row_workspace in rows:
        await cleanup_staging_file(factory, row_owner, row_workspace, identifier)
    return len(rows)


async def publish_generated_file(service, staging_id: str, filename: str, receipt: dict, *, generation_key: str | None = None):
    await service.quota._lock_global_pool()
    await service.quota._locked_account()
    item = await service.db.scalar(select(UserFileModel).where(
        UserFileModel.id == staging_id, UserFileModel.owner_user_id == service.scope.owner_user_id,
        UserFileModel.workspace_id == service.scope.workspace_id,
        UserFileModel.status == 'active').with_for_update().execution_options(populate_existing=True))
    if item is None or item.origin not in ('generation_pending', 'generated'):
        raise StorageValidationError('生成任务的暂存文件不存在')
    if item.sha256 != receipt.get('sha256') or item.size_bytes != receipt.get('size_bytes'):
        raise StorageValidationError('生成内容校验失败')
    if item.origin == 'generated':
        if item.display_name != filename:
            raise StorageValidationError('生成结果已移动或改名，请从文件空间查看')
        return item.id
    existing = await service.db.scalar(select(UserFileModel).where(
        UserFileModel.owner_user_id == service.scope.owner_user_id,
        UserFileModel.workspace_id == service.scope.workspace_id,
        UserFileModel.parent_id == item.parent_id, UserFileModel.status == 'active',
        UserFileModel.display_name == filename).with_for_update())
    if existing is not None:
        if existing.kind != 'file' or existing.origin != 'generated' or existing.sha256 != item.sha256:
            raise StorageValidationError('同名文件已存在且内容不同，未覆盖')
        await service.delete(item.id)
        await service.permanently_delete(item.id)
        return existing.id
    # Metadata publication changes no original bytes and does not duplicate storage.
    item.display_name = _safe_name(filename)
    item.origin = 'generated'
    import mimetypes
    item.mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    if generation_key:
        item.upload_key = generation_key + ':' + item.sha256
    await service.db.flush()
    await service.db.refresh(item)
    return item.id
