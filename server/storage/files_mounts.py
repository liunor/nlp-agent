"""Mount each scoped file tree once without blocking the Manager event loop."""

import asyncio
import os
from pathlib import Path
import tempfile
import threading
import subprocess

from .filesystem_bridge import mount_user_filesystem


class StorageMountRegistry:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._mounts = {}
        self._scopes = {}

    @property
    def has_mounts(self):
        return bool(self._mounts)

    def owns(self, user_id: str, workspace_id: str) -> bool:
        entry = self._mounts.get(self.path_for(user_id, workspace_id))
        return entry is not None and entry[0] is not None

    async def reap(self, active_scopes):
        async with self._lock:
            for path in list(self._mounts):
                if self._scopes[path] in active_scopes:
                    continue
                # Lazy detach also releases disconnected FUSE endpoints after
                # shutdown. Never unmount a path this registry did not own.
                result = await asyncio.to_thread(subprocess.run, ["umount", "-l", path],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                if result.returncode == 0 or not os.path.ismount(path):
                    self._mounts.pop(path, None)
                    self._scopes.pop(path, None)

    @staticmethod
    def path_for(user_id: str, workspace_id: str) -> str:
        for identifier in (user_id, workspace_id):
            if not identifier or identifier in {".", ".."} or any(c in identifier for c in "/\\"):
                raise ValueError("Invalid files bridge scope")
        base = Path(os.getenv("SANDBOX_BRIDGE_BASE_DIR", str(Path(tempfile.gettempdir()) / "sandbox_bridge")))
        return str(base / user_id / workspace_id)

    async def ensure(self, user_id: str, workspace_id: str) -> str:
        path = self.path_for(user_id, workspace_id)
        async with self._lock:
            if os.path.ismount(path):
                if path not in self._mounts:
                    raise RuntimeError("Files bridge mount belongs to another Manager; detach stale mounts before restart")
                return path
            entry = self._mounts.get(path)
            if entry is None or entry[0] is None or not entry[0].is_alive():
                Path(path).mkdir(parents=True, exist_ok=True)
                errors = []
                loop = asyncio.get_running_loop()

                def start():
                    try:
                        mount_user_filesystem(user_id=user_id, workspace_id=workspace_id,
                            session_factory=self._session_factory, mount_point=path, event_loop=loop)
                    except BaseException as error:
                        errors.append(error)

                thread = threading.Thread(target=start, daemon=True, name="sandbox-files-fuse")
                self._mounts[path] = (thread, errors)
                self._scopes[path] = (user_id, workspace_id)
                thread.start()
            else:
                thread, errors = entry
            deadline = asyncio.get_running_loop().time() + 3.0
            while not os.path.ismount(path):
                if not thread.is_alive():
                    raise RuntimeError(f"Files bridge mount failed: {errors!r}")
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("Files bridge mount timed out")
                await asyncio.sleep(0.05)
        return path
