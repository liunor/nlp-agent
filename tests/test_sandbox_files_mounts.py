import asyncio
import threading
import errno
from unittest.mock import Mock

import pytest


async def test_mount_registry_reuses_mount_and_waits_without_blocking_loop(monkeypatch, tmp_path):
    from server.storage import files_mounts
    mounted = threading.Event()
    query_ran = asyncio.Event()

    def mount(**kwargs):
        # Models a FUSE callback needing the Manager's DB event loop.
        future = asyncio.run_coroutine_threadsafe(mark_query(), kwargs["event_loop"])
        future.result(timeout=2)
        mounted.set()

    async def mark_query():
        query_ran.set()

    mount_mock = Mock(side_effect=mount)
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(files_mounts, "mount_user_filesystem", mount_mock)
    monkeypatch.setattr(files_mounts.os.path, "ismount", lambda path: mounted.is_set())
    registry = files_mounts.StorageMountRegistry(object())
    paths = await asyncio.gather(registry.ensure("user", "ws"), registry.ensure("user", "ws"))
    assert query_ran.is_set()
    assert paths == [str(tmp_path / "user/ws")] * 2
    mount_mock.assert_called_once()
    assert mount_mock.call_args.kwargs["mount_point"] == paths[0]
    unmount = Mock(return_value=type("Result", (), {"returncode": 0})())
    monkeypatch.setattr(files_mounts.subprocess, "run", unmount)
    await registry.reap({("user", "ws")})
    unmount.assert_not_called()
    await registry.reap(set())
    assert unmount.call_args.args[0] == ["umount", "-l", paths[0]]
    assert not registry.has_mounts


async def test_mount_registry_propagates_mount_failure(monkeypatch, tmp_path):
    from server.storage import files_mounts
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(files_mounts.os.path, "ismount", lambda path: False)
    monkeypatch.setattr(files_mounts, "mount_user_filesystem", Mock(side_effect=OSError("no /dev/fuse")))
    registry = files_mounts.StorageMountRegistry(object())
    with pytest.raises(RuntimeError, match="mount failed"):
        await registry.ensure("user", "ws")


async def test_mount_registry_recovers_disconnected_mount_after_restart(monkeypatch, tmp_path):
    from server.storage import files_mounts
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path))
    path = str(tmp_path / "user/ws")
    registry = files_mounts.StorageMountRegistry(object())
    dead = True
    mounted = False
    monkeypatch.setattr(registry, "_is_mount", lambda _: dead or mounted)
    monkeypatch.setattr(files_mounts.os.path, "ismount", lambda _: mounted)

    def probe(_):
        if dead:
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")

    monkeypatch.setattr(registry, "_probe_mount", probe)

    def unmount(command, **kwargs):
        nonlocal dead
        assert command == ["umount", "-l", path]
        dead = False
        return type("Result", (), {"returncode": 0})()

    def mount(**kwargs):
        nonlocal mounted
        mounted = True

    monkeypatch.setattr(files_mounts.subprocess, "run", unmount)
    monkeypatch.setattr(files_mounts, "mount_user_filesystem", mount)
    assert await registry.ensure("user", "ws") == path
    assert registry.owns("user", "ws")


async def test_mount_registry_never_detaches_a_healthy_foreign_mount(monkeypatch, tmp_path):
    from server.storage import files_mounts
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path))
    registry = files_mounts.StorageMountRegistry(object())
    monkeypatch.setattr(registry, "_is_mount", lambda _: True)
    monkeypatch.setattr(registry, "_probe_mount", lambda _: None)
    unmount = Mock()
    monkeypatch.setattr(files_mounts.subprocess, "run", unmount)
    with pytest.raises(RuntimeError, match="another Manager"):
        await registry.ensure("user", "ws")
    unmount.assert_not_called()
