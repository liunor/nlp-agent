"""Transactional platform file operations used by the scoped FUSE bridge."""
import errno
import hashlib
import io
import os
import shutil
from datetime import datetime, UTC
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import select
from starlette.datastructures import UploadFile

from server.infrastructure.mysql.models import UserFileModel
from server.rbac.service import rbac_service
from .policy import StorageBucket
from .service import StorageService, storage_path_for, _safe_name


class BridgeWrites:
    def __init__(self, owner, workspace, factory):
        self.owner, self.workspace, self.factory = owner, workspace, factory

    async def max_file_bytes(self):
        async with self.factory.begin() as session:
            principal = await rbac_service.principal_for_user_id(session, self.owner)
            service = StorageService(session, principal, self.workspace)
            return (await service._effective_policy()).max_file_bytes

    @staticmethod
    def parts(path):
        parts = PurePosixPath(path).parts
        if not path.startswith("/") or ".." in parts or "\\" in path:
            raise OSError(errno.EACCES, "Invalid file path")
        names = [p for p in parts if p != "/"]
        if any(_safe_name(p) != p for p in names):
            raise OSError(errno.EINVAL, "File name has surrounding whitespace")
        return names

    async def resolve(self, session, path):
        parent = None
        node = None
        for name in self.parts(path):
            if node is not None and node.kind != "folder":
                raise OSError(errno.ENOTDIR, "Not a directory")
            node = await session.scalar(select(UserFileModel).where(
                UserFileModel.owner_user_id == self.owner,
                UserFileModel.workspace_id == self.workspace,
                UserFileModel.parent_id == parent,
                UserFileModel.display_name == name,
                UserFileModel.status == "active"))
            if node is None:
                raise OSError(errno.ENOENT, "File not found")
            parent = node.id
        return node

    async def run(self, operation, path, **args):
        new_blob = None
        old_blob = None
        committed = False
        node_id = None
        try:
            async with self.factory.begin() as session:
                principal = await rbac_service.principal_for_user_id(session, self.owner)
                service = StorageService(session, principal, self.workspace)
                # Match quota lock order; serialize writers across Manager processes.
                await service.quota._lock_global_pool()
                await service.quota._locked_account()
                parts = self.parts(path)
                if operation in {"create", "mkdir"}:
                    if not parts:
                        raise OSError(errno.EEXIST, "Root already exists")
                    parent = await self.resolve(session, str(PurePosixPath(path).parent))
                    if parent is not None and parent.kind != "folder":
                        raise OSError(errno.ENOTDIR, "Not a directory")
                    parent_id = None if parent is None else parent.id
                    if operation == "mkdir":
                        return await service.create_folder(parts[-1], parent_id)
                    return await service.create_file(UploadFile(io.BytesIO(), filename=parts[-1]), parent_id)
                node = await self.resolve(session, path)
                if node is None:
                    raise OSError(errno.EBUSY, "Cannot modify root")
                node_id = node.id
                if operation == "utimens":
                    node.updated_at = datetime.fromtimestamp(args["mtime"], UTC).replace(tzinfo=None)
                    await session.flush()
                    return
                if operation == "delete":
                    is_dir = args["is_dir"]
                    if is_dir != (node.kind == "folder"):
                        raise OSError(errno.ENOTDIR if is_dir else errno.EISDIR, "Wrong file type")
                    if is_dir:
                        child = await session.scalar(select(UserFileModel.id).where(
                            UserFileModel.parent_id == node.id, UserFileModel.status == "active").limit(1))
                        if child:
                            raise OSError(errno.ENOTEMPTY, "Directory not empty")
                    await service.delete(node.id)
                    return
                if operation == "rename":
                    destination = args["destination"]
                    target_parts = self.parts(destination)
                    if not target_parts:
                        raise OSError(errno.EBUSY, "Cannot replace root")
                    parent = await self.resolve(session, str(PurePosixPath(destination).parent))
                    if parent is not None and parent.kind != "folder":
                        raise OSError(errno.ENOTDIR, "Not a directory")
                    ancestor = parent
                    while ancestor is not None:
                        if ancestor.id == node.id:
                            raise OSError(errno.EINVAL, "Cannot move a directory inside itself")
                        ancestor = await session.get(UserFileModel, ancestor.parent_id) if ancestor.parent_id else None
                    parent_id = None if parent is None else parent.id
                    target = await service._sibling_named(target_parts[-1], parent_id, exclude_id=node.id)
                    if target is not None:
                        if target.kind != node.kind:
                            raise OSError(errno.EISDIR if target.kind == "folder" else errno.ENOTDIR, "Wrong target type")
                        await service.delete(target.id)
                    node.parent_id = parent_id
                    node.display_name = target_parts[-1]
                    await session.flush()
                    return
                if node.kind != "file":
                    raise OSError(errno.EISDIR, "Not a file")
                policy = await service._effective_policy()
                old_blob = storage_path_for(service.scope, node.storage_key)
                old_size = node.size_bytes
                if operation == "truncate":
                    size = args["size"]
                else:
                    offset = old_size if args.get("append") else args["offset"]
                    size = max(old_size, offset + len(args["data"]))
                if size < 0:
                    raise OSError(errno.EINVAL, "Invalid size")
                if size > policy.max_file_bytes:
                    raise OSError(errno.EFBIG, "单文件大小已达到配额")
                reservation = await service.quota.reserve(StorageBucket.FILES, max(0, size-old_size),
                    resource_type="sandbox_file_write", resource_key=node.id)
                # Publish a new immutable blob, then commit its metadata pointer.
                # Failure before commit never changes the previously visible file.
                key = f"{self.workspace}/{self.owner}/{uuid4()}"
                new_blob = storage_path_for(service.scope, key)
                with old_blob.open("rb") as source, new_blob.open("xb") as target:
                    shutil.copyfileobj(source, target)
                    if operation == "truncate":
                        target.truncate(size)
                    else:
                        target.seek(offset)
                        target.write(args["data"])
                    target.flush()
                    os.fsync(target.fileno())
                node.storage_key = key
                node.size_bytes = size
                with new_blob.open("rb") as content:
                    node.sha256 = hashlib.file_digest(content, "sha256").hexdigest()
                await session.flush()
                await service.quota.finalize(reservation)
                if reservation is None:
                    await service.quota.reconcile()
            # Open reader descriptors continue to refer to the old inode on Linux.
            committed = True
            try:
                old_blob.unlink(missing_ok=True)
            except OSError:
                pass  # A retired blob is safer than invalidating a committed file.
        except BaseException:
            if new_blob is not None and not committed:
                # A lost COMMIT response is ambiguous. Never remove the new
                # blob until a fresh connection proves it was not published.
                try:
                    async with self.factory() as check:
                        published = await check.scalar(select(UserFileModel.storage_key).where(
                            UserFileModel.id == node_id,
                            UserFileModel.owner_user_id == self.owner,
                            UserFileModel.workspace_id == self.workspace))
                    if published != key:
                        new_blob.unlink(missing_ok=True)
                except BaseException:
                    pass  # Leave an orphan rather than risk deleting committed bytes.
            raise
