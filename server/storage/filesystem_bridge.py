import os
import subprocess
import stat
import errno
import asyncio
from fuse import Operations, FUSE
from sqlalchemy import select

from server.infrastructure.mysql.models import UserFileModel
from server.storage.service import storage_root

class StorageFuseBridge(Operations):
    def __init__(self, user_id: str, workspace_id: str, session_factory):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.session_factory = session_factory
        self._fh_map = {}

    def _run_async(self, coro):
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
                    "storage_key": target_node.storage_key
                }
        
        return self._run_async(_query())

    def getattr(self, path, fh=None):
        try:
            node = self._resolve_path(path)
            if node["is_dir"]:
                return {'st_mode': stat.S_IFDIR | 0o755, 'st_nlink': 2, 'st_size': 4096}
            else:
                return {'st_mode': stat.S_IFREG | 0o644, 'st_nlink': 1, 'st_size': node["size"]}
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
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        
        node = self._resolve_path(path)
        if node["is_dir"]:
            raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))
            
        if not node.get("storage_key"):
            raise OSError(errno.ENOENT, "No storage_key")
            
        real_path = str(storage_root() / node["storage_key"])

        if not os.path.exists(real_path):
            raise OSError(errno.ENOENT, f"File not found: {real_path}")

        fh = len(self._fh_map) + 1
        self._fh_map[fh] = open(real_path, 'rb')
        return fh

    def read(self, path, size, offset, fh):
        if fh not in self._fh_map:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        f = self._fh_map[fh]
        f.seek(offset)
        return f.read(size)

    def release(self, path, fh):
        if fh in self._fh_map:
            self._fh_map[fh].close()
            del self._fh_map[fh]
        return 0


def mount_user_filesystem(user_id: str, workspace_id: str, session_factory):
    base_dir = os.path.join(tempfile.gettempdir(), "sandbox_bridge")
    mount_point = os.path.join(base_dir, user_id, workspace_id)
    
    os.makedirs(mount_point, exist_ok=True)
    
    try:
        subprocess.run(
            ["umount", "-l", mount_point],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        pass
        
    print(f"[Bridge] Mounting {mount_point} for user {user_id}...")
    FUSE(
        StorageFuseBridge(user_id, workspace_id, session_factory), 
        mount_point, 
        foreground=True, 
        allow_other=True
    )