"""Reproduce abrupt FUSE daemon death in a dedicated diagnostic mount tree."""
import asyncio
import os
import sys
from pathlib import Path

from configs.settings import settings
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.storage.files_mounts import StorageMountRegistry


async def main(owner=None, workspace=None):
    engine = create_engine(DatabaseConfig.from_runtime(settings.database_runtime))
    registry = StorageMountRegistry(create_session_factory(engine))
    if owner is None:
        owner, workspace = sys.argv[-2:]
    child = None
    try:
        if "--daemon" in sys.argv:
            await registry.ensure(owner, workspace)
            print("MOUNT_READY", flush=True)
            await asyncio.Event().wait()
        child = await asyncio.create_subprocess_exec(sys.executable, "-B", __file__,
            "--daemon", owner, workspace, stdout=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])
                 + os.pathsep + os.environ.get("PYTHONPATH", "")})
        assert await asyncio.wait_for(child.stdout.readline(), 15) == b"MOUNT_READY\n"
        child.kill()
        await child.wait()
        path = registry.path_for(owner, workspace)
        assert registry._is_mount(path), "dead FUSE root must remain in mountinfo"
        try:
            await asyncio.to_thread(os.stat, path)
        except OSError as error:
            assert error.errno == 107, error
        else:
            raise AssertionError("FUSE endpoint did not disconnect")
        restored = await registry.ensure(owner, workspace)
        assert restored == path
        assert isinstance(await asyncio.to_thread(os.listdir, restored), list)
        print("ABRUPT_DAEMON_DEATH_REMOUNT_READ_OK")
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        await registry.reap(set())
        await engine.dispose()


if __name__ == "__main__":
    # Require a unique diagnostic subtree; never kill an application Manager.
    assert "restart-probe-" in Path(os.environ["SANDBOX_BRIDGE_BASE_DIR"]).name
    asyncio.run(main())
