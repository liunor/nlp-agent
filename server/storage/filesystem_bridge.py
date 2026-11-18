import os
import stat
import errno
import asyncio
import threading
import time
from datetime import UTC
from sqlalchemy import select

from server.infrastructure.mysql.models import UserFileModel
from server.storage.service import StorageScope, StorageValidationError, storage_path_for
from server.storage.service import StorageNameConflict, StorageQuotaExceeded
from .bridge_writes import BridgeWrites

class StorageFuseBridge:
    """File operations independent of libfuse, which is loaded only at mount time."""

    def __init__(self, user_id: str, workspace_id: str, session_factory, *, event_loop=None):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.session_factory = session_factory
        self._fh_map = {}
        self._next_fh = 1
        self._event_loop = event_loop
        self._handles_lock = threading.RLock()
        self.writable = os.getenv("SANDBOX_FILES_BRIDGE_WRITABLE", "false").lower() == "true"
        self._writer = BridgeWrites(user_id, workspace_id, session_factory)
        self._write_handles = {}

    def _mutate(self, operation, path, **args):
        if not self.writable:
            raise OSError(errno.EROFS, "Read-only file bridge")
        try:
            return self._run_async(self._writer.run(operation, path, **args))
        except StorageQuotaExceeded as error:
            raise OSError(errno.EDQUOT, str(error)) from error
        except StorageNameConflict as error:
            raise OSError(errno.EEXIST, str(error)) from error
        except (StorageValidationError, PermissionError) as error:
            raise OSError(errno.EACCES, str(error)) from error

    def _run_async(self, coro):
        if self._event_loop is not None:
            # The pooled MySQL connections belong to the Manager loop. Never
            # move them into a fresh asyncio.run() loop for each FUSE callback.
            future = asyncio.run_coroutine_threadsafe(coro, self._event_loop)
            try:
                return future.result(timeout=30)
            except TimeoutError:
                future.cancel()
                raise OSError(errno.EIO, "File metadata query timed out")
        try:
            return asyncio.run(coro)
        except Exception as e:
            print(f"[FUSE Error] {e}")
            raise

    def _resolve_path(self, path: str) -> dict:
        if path == "/":
            return {"id": None, "name": "/", "is_dir": True, "size": 0, "storage_key": None}

        parts = [p for p in path.strip("/").split("/") if p]

        async def _query():
            async with self.session_factory() as session:
                current_parent_id = None
                target_node = None

                for part in parts:
                    stmt = select(UserFileModel).where(
                        UserFileModel.owner_user_id == self.user_id,
                        UserFileModel.workspace_id == self.workspace_id,
                        UserFileModel.parent_id == current_parent_id,
                        UserFileModel.display_name == part,
                        UserFileModel.status == 'active'
                    )
                    result = await session.execute(stmt)
                    target_node = result.scalar_one_or_none()

                    if not target_node:
                        raise FileNotFoundError(f"Not found: {part}")

                    current_parent_id = target_node.id

                return {
                    "id": target_node.id,
                    "name": target_node.display_name,
                    "is_dir": target_node.kind == "folder",
                    "size": target_node.size_bytes,
                    "storage_key": target_node.storage_key,
                    "mtime": target_node.updated_at.replace(tzinfo=UTC).timestamp() if target_node.updated_at else 0,
                }

        return self._run_async(_query())

    def getattr(self, path, fh=None):
        try:
            node = self._resolve_path(path)
            if node["is_dir"]:
                return {'st_mode': stat.S_IFDIR | 0o755, 'st_uid': 10001, 'st_gid': 10001, 'st_mtime': node.get("mtime", 0), 'st_nlink': 2, 'st_size': 4096}
            else:
                return {'st_mode': stat.S_IFREG | 0o644, 'st_uid': 10001, 'st_gid': 10001, 'st_mtime': node.get("mtime", 0), 'st_nlink': 1, 'st_size': node["size"]}
        except FileNotFoundError:
            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    def readdir(self, path, fh):
        try:
            node = self._resolve_path(path)
            if not node["is_dir"]:
                raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))

            async def _query_list():
                async with self.session_factory() as session:
                    stmt = select(UserFileModel.display_name).where(
                        UserFileModel.owner_user_id == self.user_id,
                        UserFileModel.workspace_id == self.workspace_id,
                        UserFileModel.parent_id == node["id"],
                        UserFileModel.status == 'active'
                    )
                    result = await session.execute(stmt)
                    file_names = result.scalars().all()
                    return ['.', '..'] + list(file_names)

            return self._run_async(_query_list())
        except FileNotFoundError:
            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    def open(self, path, flags):
        if (flags & 3) != os.O_RDONLY:
            if not self.writable:
                raise OSError(errno.EROFS, os.strerror(errno.EROFS))
            node = self._resolve_path(path)
            if node["is_dir"]:
                raise OSError(errno.EISDIR, "Not a file")
            if flags & os.O_TRUNC:
                self._mutate("truncate", path, size=0)
            with self._handles_lock:
                fh = self._next_fh
                self._next_fh += 1
                self._write_handles[fh] = flags
                return fh

        node = self._resolve_path(path)
        if node["is_dir"]:
            raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

        if not node.get("storage_key"):
            raise OSError(errno.ENOENT, "No storage_key")

        try:
            real_path = str(storage_path_for(
                StorageScope(self.user_id, self.workspace_id, policy=None), node["storage_key"]
            ))
        except StorageValidationError as error:
            raise OSError(errno.EACCES, "Invalid file storage location") from error

        if not os.path.exists(real_path):
            raise OSError(errno.ENOENT, f"File not found: {real_path}")

        with self._handles_lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fh_map[fh] = open(real_path, 'rb')
        return fh

    def read(self, path, size, offset, fh):
        with self._handles_lock:
            if fh in self._write_handles:
                if self._write_handles[fh] & os.O_ACCMODE == os.O_WRONLY:
                    raise OSError(errno.EBADF, "Handle is not readable")
                read_fh = self.open(path, os.O_RDONLY)
                try:
                    return self.read(path, size, offset, read_fh)
                finally:
                    self.release(path, read_fh)
            if fh not in self._fh_map:
                raise OSError(errno.EBADF, os.strerror(errno.EBADF))
            f = self._fh_map[fh]
            f.seek(offset)
            return f.read(size)

    def release(self, path, fh):
        with self._handles_lock:
            self._write_handles.pop(fh, None)
            if fh in self._fh_map:
                self._fh_map[fh].close()
                del self._fh_map[fh]
        return 0

    def create(self, path, mode, fi=None):
        self._mutate("create", path)
        return self.open(path, os.O_RDWR if fi is None else fi.flags)

    def write(self, path, data, offset, fh):
        with self._handles_lock:
            if fh not in self._write_handles:
                raise OSError(errno.EBADF, "Handle is not writable")
            if offset < 0:
                raise OSError(errno.EINVAL, "Invalid offset")
            self._mutate("write", path, data=data, offset=offset,
                         append=bool(self._write_handles[fh] & os.O_APPEND))
            return len(data)

    def truncate(self, path, length, fh=None):
        self._mutate("truncate", path, size=length)
        return 0

    def mkdir(self, path, mode):
        self._mutate("mkdir", path)
        return 0

    def unlink(self, path):
        self._mutate("delete", path, is_dir=False)
        return 0

    def rmdir(self, path):
        self._mutate("delete", path, is_dir=True)
        return 0

    def rename(self, old, new):
        self._mutate("rename", old, destination=new)
        return 0

    def flush(self, path, fh):
        return 0  # Each write is fsynced and committed before it returns.

    def fsync(self, path, datasync, fh):
        return self.flush(path, fh)

    def chown(self, path, uid, gid):
        # The platform has one fixed UID/GID view; gofer sets these after create.
        if not self.writable:
            raise OSError(errno.EROFS, "Read-only file bridge")
        if uid not in (-1, 10001) or gid not in (-1, 10001):
            raise OSError(errno.EPERM, "File ownership is managed by the platform")
        self._resolve_path(path)
        return 0

    def chmod(self, path, mode):
        if not self.writable:
            raise OSError(errno.EROFS, "Read-only file bridge")
        node = self._resolve_path(path)
        if mode & 0o7777 != (0o755 if node["is_dir"] else 0o644):
            raise OSError(errno.EOPNOTSUPP, "File modes are managed by the platform")
        return 0

    def utimens(self, path, times=None):
        self._mutate("utimens", path, mtime=time.time() if times is None else times[1])
        return 0

    def symlink(self, target, source):
        raise OSError(errno.EOPNOTSUPP, "Platform files do not support symbolic links")

    def link(self, target, source):
        raise OSError(errno.EOPNOTSUPP, "Platform files do not support hard links")


def mount_user_filesystem(user_id: str, workspace_id: str, session_factory, *, mount_point: str, event_loop):
    from fuse import FUSE, Operations

    class BridgeOperations(StorageFuseBridge, Operations):
        pass

    FUSE(
        BridgeOperations(user_id, workspace_id, session_factory, event_loop=event_loop),
        mount_point,
        foreground=True,
        allow_other=True,
        ro=os.getenv("SANDBOX_FILES_BRIDGE_WRITABLE", "false").lower() != "true",
        attr_timeout=0,
        entry_timeout=0,
        negative_timeout=0,
        direct_io=True,
        nothreads=True,
    )
