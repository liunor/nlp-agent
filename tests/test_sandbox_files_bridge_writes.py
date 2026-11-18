import errno
import os
from unittest.mock import AsyncMock, Mock

import pytest

from server.storage.bridge_writes import BridgeWrites
from server.storage.filesystem_bridge import StorageFuseBridge
from server.storage.service import StorageQuotaExceeded


@pytest.fixture
def writable(monkeypatch):
    monkeypatch.setenv("SANDBOX_FILES_BRIDGE_WRITABLE", "true")
    bridge = StorageFuseBridge("user", "workspace", None)
    bridge._writer.run = AsyncMock()
    bridge._resolve_path = Mock(return_value={"id": "f", "is_dir": False, "size": 0})
    return bridge


def test_write_append_truncate_and_close(writable):
    handle = writable.open("/文件.txt", os.O_RDWR | os.O_APPEND)
    assert writable.write("/文件.txt", "中文".encode(), 0, handle) == 6
    writable._writer.run.assert_awaited_with("write", "/文件.txt", data="中文".encode(), offset=0, append=True)
    writable.truncate("/文件.txt", 2, handle)
    writable._writer.run.assert_awaited_with("truncate", "/文件.txt", size=2)
    writable.release("/文件.txt", handle)
    with pytest.raises(OSError) as error:
        writable.write("/文件.txt", b"x", 0, handle)
    assert error.value.errno == errno.EBADF


def test_create_mkdir_move_and_trash(writable):
    writable.create("/new.txt", 0o644)
    writable._writer.run.assert_awaited_with("create", "/new.txt")
    writable.mkdir("/目录", 0o755)
    writable._writer.run.assert_awaited_with("mkdir", "/目录")
    writable.rename("/new.txt", "/目录/new.txt")
    writable._writer.run.assert_awaited_with("rename", "/new.txt", destination="/目录/new.txt")
    writable.unlink("/目录/new.txt")
    writable._writer.run.assert_awaited_with("delete", "/目录/new.txt", is_dir=False)


def test_quota_failure_is_reported_to_python(writable):
    writable._writer.run.side_effect = StorageQuotaExceeded("full")
    handle = writable.open("/file", os.O_WRONLY)
    with pytest.raises(OSError) as error:
        writable.write("/file", b"data", 0, handle)
    assert error.value.errno == errno.EDQUOT


def test_owner_can_write_but_mount_exposes_no_other_user(writable):
    attr = writable.getattr("/file")
    assert attr["st_uid"] == 10001
    assert attr["st_mode"] & 0o777 == 0o644


@pytest.mark.parametrize("path", ["../secret", "/../secret", "/dir/../../secret", "/dir\\secret"])
def test_write_path_rejects_escape(path):
    with pytest.raises(OSError) as error:
        BridgeWrites.parts(path)
    assert error.value.errno == errno.EACCES
