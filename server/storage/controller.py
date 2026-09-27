"""Account-owned file manager API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import StorageFolderRequest, StorageQuotaUpdateRequest, StorageRenameRequest
from .service import (
    StorageError,
    StorageNameConflict,
    StorageQuotaExceeded,
    StorageAdminService,
    StorageService,
    StorageValidationError,
)

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
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error))
    if isinstance(error, StorageNameConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


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
    workspace_id: str | None = Form(default=None),
) -> dict:
    service = _service(db, principal, workspace_id)
    try:
        return await service.create_file(file, parent_id)
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
) -> FileResponse:
    service = _service(db, principal, workspace_id)
    try:
        item, path = await service.download_path(file_id)
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return FileResponse(
        path=path,
        media_type=item.mime_type or "application/octet-stream",
        filename=item.display_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )
