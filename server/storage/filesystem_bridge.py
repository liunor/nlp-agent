import os
import subprocess
import stat
import errno
import asyncio
from fuse import Operations, FUSE
from pathlib import Path

from server.storage.service import StorageService

class StorageFuseBridge(Operations):
    def __init__(self, user_id: str, workspace_id: str, session_factory):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.session_factory = session_factory
        self._fh_map = {}  # 保存打开的文件句柄

    def _run_async(self, coro):
        try:
            return asyncio.run(coro)
        except Exception as e:
            print(f"[FUSE Error] {e}")
            raise

    def _resolve_path(self, path: str) -> dict:
        if path == "/":
            return {"id": None, "name": "/", "is_dir": True, "size": 0}
            
        parts = [p for p in path.strip("/").split("/") if p]
        
        async def _query():
            async with self.session_factory() as session:
                current_parent_id = None
                target_node = None
                
                for part in parts:
                    files = await StorageService.list_files(session, parent_id=current_parent_id)
                    match = next((f for f in files if f.get("name") == part or f.get("display_name") == part), None)
                    if not match:
                        raise FileNotFoundError
                        
                    target_node = match
                    current_parent_id = match["id"]
                real_file = await StorageService.get_file(session, target_node["id"])
                return {
                    "id": real_file.id,
                    "name": real_file.name,
                    "is_dir": real_file.is_directory, 
                    "size": real_file.size_bytes     
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
                    # TODO: 调用真实的 list_files
                    files = await StorageService.list_files(session, parent_id=node["id"])
                    return ['.', '..'] + [f.get("name") or f.get("display_name") for f in files]
            
            return self._run_async(_query_list())
        except FileNotFoundError:
            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    def open(self, path, flags):
        if (flags & 3) != os.O_RDONLY:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        
        node = self._resolve_path(path)
        if node["is_dir"]:
            raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))
            
        async def _get_path():
            async with self.session_factory() as session:
                _, real_path = await StorageService.download_path(session, node["id"])
                return real_path
                
        real_path = self._run_async(_get_path())
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
    """供 Sandbox Manager 启动挂载的入口函数"""
    mount_point = f"/data/bridge/{user_id}/{workspace_id}"
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