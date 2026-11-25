"""Confirmation and bounded recovery for interrupted chat uploads."""
from datetime import UTC, datetime, timedelta
from sqlalchemy import select
from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import UserFileModel
from server.storage.service import StorageService, StorageValidationError


async def confirm_upload(service, file_id, *, existing_file=False):
    await service.quota._lock_global_pool()
    await service.quota._locked_account()
    item = await service.db.scalar(select(UserFileModel).where(
        UserFileModel.id == file_id, UserFileModel.owner_user_id == service.scope.owner_user_id,
        UserFileModel.workspace_id == service.scope.workspace_id,
        UserFileModel.status == 'active').with_for_update().execution_options(populate_existing=True))
    if item is None or item.kind != 'file' or (not existing_file and item.origin not in ('chat_pending', 'chat')):
        raise StorageValidationError('待确认上传不存在')
    if item.origin == 'chat_pending':
        item.origin = 'chat'
    await service.db.flush()
    await service.db.refresh(item)
    return service.serialize(item)


async def purge_expired_uploads(factory):
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    async with factory() as db:
        rows = (await db.execute(select(UserFileModel.id, UserFileModel.owner_user_id,
            UserFileModel.workspace_id).where(UserFileModel.origin == 'chat_pending',
            UserFileModel.status == 'active', UserFileModel.created_at < cutoff).limit(20))).all()
    for identifier, owner, workspace in rows:
        async with factory.begin() as db:
            service = StorageService(db, AuthenticatedPrincipal(user_id=owner, workspace_ids={workspace}), workspace)
            await service.quota._lock_global_pool()
            await service.quota._locked_account()
            item = await db.scalar(select(UserFileModel).where(UserFileModel.id == identifier,
                UserFileModel.origin == 'chat_pending', UserFileModel.status == 'active',
                UserFileModel.created_at < cutoff).with_for_update())
            if item is None:
                continue
            # A rename, move or write is an explicit use: preserve that original.
            if item.updated_at > item.created_at:
                item.origin = 'chat'
                continue
            await service.delete(item.id)
            await service.permanently_delete(item.id)
    return len(rows)
