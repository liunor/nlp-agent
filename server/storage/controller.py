"""Account-owned file manager API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import FileDistributionRequest, FileTransferRequest, StorageFolderRequest, StorageQuotaUpdateRequest, StorageRenameRequest
from .distribution_service import (
    DistributionError,
    DistributionPermissionError,
    DistributionTargetError,
    FileDistributionService,
)
from .service import (
    StorageError,
    StorageNameConflict,
    StorageQuotaExceeded,
    StorageAdminService,
    StoragePreviewUnsupported,
    StorageService,
    StorageValidationError,
)
from .transfer_service import FileTransferService, TransferConflict, TransferNotFound

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _workspace(principal, workspace_id: str | None) -> str:
    if workspace_id:
        return workspace_id
    available = sorted(item for item in principal.workspace_ids if item != "*")
    return available[0] if available else "default"


def _service(db: AsyncSession, principal, workspace_id: str | None) -> StorageService:
    try:
        return StorageService(db, principal, _workspace(principal, workspace_id))
    except StorageValidationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


def _write_error(error: StorageError) -> HTTPException:
    if isinstance(error, StorageQuotaExceeded):
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=error.public_message)
    if isinstance(error, StorageNameConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, TransferConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, TransferNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, StoragePreviewUnsupported):
        return HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def _transfer_service(db: AsyncSession, principal, workspace_id: str | None = None) -> FileTransferService:
    try:
        return FileTransferService(db, principal, workspace_id)
    except StorageValidationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


def _admin_service(db: AsyncSession, principal) -> StorageAdminService:
    try:
        return StorageAdminService(db, principal)
    except StorageValidationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


@router.get("/admin/usage")
async def get_admin_storage_usage(db: DbSession, principal: Principal) -> dict:
    return {"items": await _admin_service(db, principal).usage()}


@router.patch("/admin/accounts/{user_id}/quota")
async def update_admin_storage_quota(
    user_id: str,
    body: StorageQuotaUpdateRequest,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
) -> dict:
    try:
        return await _admin_service(db, principal).update_quota(
            user_id,
            body.model_dump(exclude_unset=True),
            actor_user_id=principal.user_id,
            reason=body.reason,
        )
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/admin/trash/purge")
async def purge_admin_storage_trash(
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
) -> dict:
    try:
        removed = await _admin_service(db, principal).purge_expired_trash()
        return {"removed": removed}
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/usage")
async def get_storage_usage(
    db: DbSession,
    principal: Principal,
    workspace_id: str | None = Query(default=None),
) -> dict:
    return await _service(db, principal, workspace_id).usage()


@router.get("/files")
async def list_storage_files(
    db: DbSession,
    principal: Principal,
    workspace_id: str | None = Query(default=None),
    parent_id: str | None = Query(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return {"items": await service.list_files(parent_id)}
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/trash")
async def list_storage_trash(
    db: DbSession,
    principal: Principal,
    workspace_id: str | None = Query(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return {"items": await service.list_trash()}
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/transfer-recipients/{identity_id}")
async def lookup_transfer_recipient(identity_id: str, db: DbSession, principal: Principal) -> dict:
    try:
        return await _transfer_service(db, principal).lookup_recipient(identity_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers/preflight")
async def preflight_file_transfer(body: FileTransferRequest, db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    try:
        return await _transfer_service(db, principal, body.workspace_id).preflight(body.source_file_id, body.recipient_identity_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
async def create_file_transfer(body: FileTransferRequest, db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    try:
        return await _transfer_service(db, principal, body.workspace_id).create(body.source_file_id, body.recipient_identity_id, body.idempotency_key)
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/transfers/summary")
async def get_file_transfer_summary(db: DbSession, principal: Principal) -> dict:
    return await _transfer_service(db, principal).summary()


@router.get("/transfers/unread-summary")
async def get_file_transfer_unread_summary(db: DbSession, principal: Principal) -> dict:
    return await _transfer_service(db, principal).unread_summary()


@router.post("/transfers/mark-seen")
async def mark_file_transfers_seen(db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    return await _transfer_service(db, principal).mark_seen()


@router.get("/transfers")
async def list_file_transfers(db: DbSession, principal: Principal, box: str = Query(default="incoming")) -> dict:
    try:
        return {"items": await _transfer_service(db, principal).list(box)}
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers/{transfer_id}/accept")
async def accept_file_transfer(transfer_id: str, db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    try:
        return await _transfer_service(db, principal).accept(transfer_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers/{transfer_id}/reject")
async def reject_file_transfer(transfer_id: str, db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    try:
        return await _transfer_service(db, principal).reject(transfer_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers/{transfer_id}/cancel")
async def cancel_file_transfer(transfer_id: str, db: DbSession, principal: Principal, _write: WriteClaims) -> dict:
    try:
        return await _transfer_service(db, principal).cancel(transfer_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/distribution-targets")
async def get_distribution_targets(db: DbSession, principal: Principal) -> dict:
    try:
        service = FileDistributionService(db, principal)
        return await service.get_distribution_targets()
    except DistributionPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/transfers/distribute", status_code=status.HTTP_201_CREATED)
async def distribute_file(
    body: FileDistributionRequest,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
) -> dict:
    try:
        service = FileDistributionService(db, principal, body.workspace_id)
        return await service.distribute_file(
            source_file_id=body.source_file_id,
            target_type=body.target_type,
            target_value=body.target_value,
            direct_delivery=body.direct_delivery,
        )
    except DistributionPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except DistributionTargetError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/distribution-history")
async def get_distribution_history(
    db: DbSession,
    principal: Principal,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    try:
        service = FileDistributionService(db, principal)
        return {"items": await service.list_distribution_history(limit)}
    except DistributionPermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except StorageError as error:
        raise _write_error(error) from error



@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_storage_folder(
    body: StorageFolderRequest,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    workspace_id: str | None = Query(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return await service.create_folder(body.name, body.parent_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/files", status_code=status.HTTP_201_CREATED)
async def upload_storage_file(
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    file: UploadFile = File(...),
    parent_id: str | None = Form(default=None),
    idempotency_key: str | None = Form(default=None, max_length=128),
    workspace_id: str | None = Form(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        if idempotency_key:
            return await service.create_file(file, parent_id, upload_key=idempotency_key, origin="chat_pending")
        return await service.create_file(file, parent_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/files/{file_id}/confirm")
async def confirm_storage_upload(file_id: str, db: DbSession, principal: Principal,
                                 _write: WriteClaims, workspace_id: str | None = Query(default=None)) -> dict:
    from server.storage.uploads import confirm_upload
    try:
        return await confirm_upload(_service(db, principal, workspace_id), file_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.patch("/files/{file_id}")
async def rename_storage_file(
    file_id: str,
    body: StorageRenameRequest,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    workspace_id: str | None = Query(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return await service.rename(file_id, body.name)
    except StorageError as error:
        raise _write_error(error) from error


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_storage_file(
    file_id: str,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    workspace_id: str | None = Query(default=None),
) -> None:
    service = _service(db, principal, workspace_id)
    try:
        await service.delete(file_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.post("/trash/{file_id}/restore")
async def restore_storage_file(
    file_id: str,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    workspace_id: str | None = Query(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return await service.restore(file_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.delete("/trash/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_storage_file(
    file_id: str,
    db: DbSession,
    principal: Principal,
    _write: WriteClaims,
    workspace_id: str | None = Query(default=None),
) -> None:
    service = _service(db, principal, workspace_id)
    try:
        await service.permanently_delete(file_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/files/{file_id}/download")
async def download_storage_file(
    file_id: str,
    db: DbSession,
    principal: Principal,
    workspace_id: str | None = Query(default=None),
    expected_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
) -> FileResponse:
    service = _service(db, principal, workspace_id)
    try:
        item, path = await service.download_path(file_id)
        if expected_hash and item.sha256 != expected_hash:
            raise HTTPException(status_code=409, detail="文件版本已变化，请先查看当前版本")
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return FileResponse(
        path=path,
        media_type=item.mime_type or "application/octet-stream",
        filename=item.display_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/files/{file_id}/preview")
async def preview_storage_file(
    file_id: str,
    db: DbSession,
    principal: Principal,
    workspace_id: str | None = Query(default=None),
) -> dict:
    try:
        return await _service(db, principal, workspace_id).preview(file_id)
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/files/{file_id}/content")
async def read_storage_content(
    file_id: str, db: DbSession, principal: Principal,
    workspace_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=100),
    expected_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    char_offset: int = Query(default=0, ge=0, le=25 * 1024 * 1024),
) -> dict:
    from server.documents.content import DocumentError
    try:
        return await _service(db, principal, workspace_id).read_content(
            file_id, offset=offset, limit=limit, expected_hash=expected_hash, char_offset=char_offset)
    except DocumentError as error:
        raise HTTPException(status_code=409 if error.code == "version_changed" else 415,
                            detail=str(error)) from error
    except StorageError as error:
        raise _write_error(error) from error


@router.get("/files/{file_id}/view", response_model=None)
async def view_storage_file(
    file_id: str, db: DbSession, principal: Principal,
    workspace_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0, le=100_000),
    char_offset: int = Query(default=0, ge=0, le=25 * 1024 * 1024),
    expected_hash: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
) -> FileResponse | HTMLResponse:
    """Inline only validated raster images/PDF; never serve HTML/SVG as app pages."""
    service = _service(db, principal, workspace_id)
    try:
        item, path = await service.download_path(file_id)
        if expected_hash and item.sha256 != expected_hash:
            from html import escape
            from urllib.parse import quote, urlencode
            current_url = '/api/v1/storage/files/' + quote(file_id, safe='') + '/view?' + urlencode({'workspace_id': service.scope.workspace_id})
            return HTMLResponse('<!doctype html><meta charset="utf-8"><p>文件内容已变化，此附件引用的旧版本不再可用。</p>'
                + '<a href="' + escape(current_url) + '">查看当前版本</a>', status_code=409,
                headers={'Cache-Control': 'private, no-store', 'Content-Security-Policy': "default-src 'none'; base-uri 'none'; form-action 'none'"})
        from pathlib import Path
        suffix = Path(item.display_name).suffix.lower()
        if suffix not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}:
            from html import escape
            from urllib.parse import urlencode
            from server.documents.content import DocumentError
            try:
                result = await service.read_content(file_id, offset=offset, char_offset=char_offset, expected_hash=expected_hash)
            except DocumentError as error:
                raise StoragePreviewUnsupported(str(error)) from error
            body = ''.join(f'<section><small>段/表 {b["number"]}</small><pre>{escape(b["text"])}</pre></section>' for b in result['blocks'])
            if result['next_offset'] is not None:
                query = urlencode(dict(offset=result['next_offset'], char_offset=result['next_char_offset'], workspace_id=service.scope.workspace_id, expected_hash=result['sha256']))
                body += f'<a href="?{escape(query)}">继续读取</a>'
            return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                + '<meta name="viewport" content="width=device-width"><title>' + escape(item.display_name)
                + '</title><style>body{max-width:900px;margin:24px auto;padding:16px;font:16px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#666}</style>'
                + '<h1>' + escape(item.display_name) + '</h1><p>阅读视图，排版可能与 Word 不同；图片和图表未提取。</p>' + body + '</html>',
                headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                         'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})
        if suffix == '.pdf':
            with path.open('rb') as stream:
                if not stream.read(5).startswith(b'%PDF-'):
                    raise StoragePreviewUnsupported('文件不是有效的 PDF')
            media_type = 'application/pdf'
        else:
            from server.tools.vision.safety import load_validated_image, ImageSafetyLimits
            from server.tools.vision.contracts import VisionError
            try:
                image = load_validated_image(path, ImageSafetyLimits())
            except VisionError as error:
                raise StoragePreviewUnsupported('该文件不支持原件预览') from error
            media_type = image.reference.media_type
        return FileResponse(path, media_type=media_type, filename=item.display_name,
            content_disposition_type='inline', headers={
                'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
                'Content-Security-Policy': "sandbox; default-src 'none'"})
    except StorageError as error:
        raise _write_error(error) from error
