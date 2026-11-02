import pytest

try:
    from fuse import FUSE, Operations
except (ImportError, OSError) as e:
    pytest.skip(f"Skipping FUSE tests: {e}", allow_module_level=True)

from unittest.mock import patch
import stat
import errno

from server.storage.filesystem_bridge import StorageFuseBridge
from server.infrastructure.mysql.models import UserFileModel

@pytest.fixture(autouse=True)
def mock_fuse_and_env(monkeypatch):
    with patch("server.sandbox.manager.mount_user_filesystem") as mock_mount:
        with patch("os.path.ismount", return_value=True):
            yield mock_mount

class FakeResult:
    def __init__(self, value):
        self.value = value
    def scalar_one_or_none(self):
        return self.value
    def scalars(self):
        class ScalarWrapper:
            def __init__(self, vals): self.vals = vals
            def all(self): return self.vals
        return ScalarWrapper(self.value)

class FakeSession:
    def __init__(self, return_values):
        self.return_values = return_values
        self.call_count = 0
    async def execute(self, stmt):
        val = self.return_values[self.call_count]
        self.call_count += 1
        return FakeResult(val)
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

def mock_session_factory(return_values):
    return lambda: FakeSession(return_values)

@pytest.fixture
def mock_models():
    folder = UserFileModel(
        id="dir-1", display_name="数据", kind="folder", size_bytes=0, parent_id=None, status="active"
    )
    file = UserFileModel(
        id="file-1", display_name="students.csv", kind="file", size_bytes=25, parent_id="dir-1", 
        storage_key="ws/user/file-1", status="active"
    )
    return folder, file

def test_resolve_path_success(mock_models):
    folder, file = mock_models
    factory = mock_session_factory([folder, file])
    bridge = StorageFuseBridge(user_id="u1", workspace_id="w1", session_factory=factory)
    
    node = bridge._resolve_path("/数据/students.csv")
    
    assert node["id"] == "file-1"
    assert node["name"] == "students.csv"
    assert node["is_dir"] is False
    assert node["size"] == 25

def test_readdir(mock_models):
    folder, file = mock_models
    with patch.object(StorageFuseBridge, "_resolve_path", return_value={"id": "dir-1", "is_dir": True}):
        factory = mock_session_factory([["students.csv"]])
        bridge = StorageFuseBridge(user_id="u1", workspace_id="w1", session_factory=factory)
        
        files = bridge.readdir("/数据", fh=None)
        assert "students.csv" in files
        assert "." in files and ".." in files

def test_getattr_file():
    bridge = StorageFuseBridge(user_id="u1", workspace_id="w1", session_factory=None)
    with patch.object(bridge, "_resolve_path", return_value={"is_dir": False, "size": 25}):
        attr = bridge.getattr("/students.csv")
        assert stat.S_ISREG(attr['st_mode'])
        assert attr['st_size'] == 25

def test_open_readonly():
    bridge = StorageFuseBridge(user_id="u1", workspace_id="w1", session_factory=None)
    with pytest.raises(OSError) as exc_info:
        bridge.open("/students.csv", flags=1) 
    assert exc_info.value.errno == errno.EROFS

def test_read_content(tmp_path):
    bridge = StorageFuseBridge(user_id="u1", workspace_id="w1", session_factory=None)
    
    with patch.object(bridge, "_resolve_path", return_value={"id": "file-1", "is_dir": False, "storage_key": "ws/user/file-1"}), \
         patch("server.storage.filesystem_bridge.storage_root") as mock_storage_root:
        
        mock_storage_root.return_value = tmp_path
        expected_path = tmp_path / "ws/user/file-1"
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_bytes(b"id,name,score\n1,Alice,95")

        fh = bridge.open("/students.csv", flags=0)
        content = bridge.read("/students.csv", size=100, offset=0, fh=fh)
        assert b"Alice" in content
        bridge.release("/students.csv", fh)

