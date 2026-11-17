"""Read an existing platform file through FUSE and runsc; never mutate file data."""
import asyncio
import errno
import json
import os
from uuid import uuid4

from sqlalchemy import select

from configs.settings import settings
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import UserFileModel
from server.storage.files_mounts import StorageMountRegistry
from server.storage.filesystem_bridge import StorageFuseBridge


async def main():
    engine = create_engine(DatabaseConfig.from_runtime(settings.database_runtime))
    factory = create_session_factory(engine)
    registry = StorageMountRegistry(factory)
    try:
        async with factory() as session:
            node = await session.scalar(select(UserFileModel).where(
                UserFileModel.status == "active", UserFileModel.kind == "file").limit(1))
            if node is None:
                raise RuntimeError("Upload a platform file before running the bridge probe")
            parts = [node.display_name]
            parent_id = node.parent_id
            while parent_id:
                parent = await session.get(UserFileModel, parent_id)
                assert parent.owner_user_id == node.owner_user_id
                assert parent.workspace_id == node.workspace_id
                parts.insert(0, parent.display_name)
                parent_id = parent.parent_id
            owner, workspace = node.owner_user_id, node.workspace_id
        path = "/" + "/".join(parts)
        isolated = StorageFuseBridge(str(uuid4()), workspace, factory,
                                   event_loop=asyncio.get_running_loop())
        try:
            await asyncio.to_thread(isolated._resolve_path, path)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Cross-owner file lookup unexpectedly succeeded")
        mount = await registry.ensure(owner, workspace)
        code = (
            "import os,errno; p=" + repr("/files" + path) + "; "
            "f=open(p,'rb'); data=f.read(64); f.close(); "
            "print('READ_OK bytes=',len(data)); "
            "print('DIRECTORY_OK',isinstance(os.listdir('/files'),list))\n"
            "try:\n open(p,'r+b')\n"
            "except OSError as e:\n assert e.errno in (errno.EROFS,errno.EACCES); print('READONLY_OK')\n"
            "else:\n raise AssertionError('Writable files mount')\n"
        )
        process = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--runtime", "runsc", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--user", "10001:10001", "--memory", "128m", "--cpus", "0.25",
            "--mount", f"type=bind,source={mount},target=/files,readonly",
            os.environ["BRIDGE_PROBE_IMAGE"], "python", "-B", "-c", code)
        assert await process.wait() == 0, "runsc file read failed"
        print(json.dumps({"read": "passed", "readonly": "passed", "owner_isolation": "passed"}))
    finally:
        await registry.reap(set())
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
