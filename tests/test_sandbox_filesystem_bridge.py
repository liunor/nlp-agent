import pytest
from unittest.mock import AsyncMock, patch
import stat
import errno
from server.storage.filesystem_bridge import StorageFuseBridge

MOCK_FILES = {
    None: [
        {"id": "dir-1", "name": "数据", "is_directory": True, "size_bytes": 0}
    ],
    "dir-1": [
        {"id": "file-1", "name": "students.csv", "is_directory": False, "size_bytes": 25}
    ]
}

@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    class SessionCtx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
    factory = lambda: SessionCtx()
    return factory, session

@pytest.fixture
def bridge(mock_session_factory):
    factory, _ = mock_session_factory
    return StorageFuseBridge(user_id="user-123", workspace_id="ws-456", session_factory=factory)

def test_resolve_path_success(bridge, mock_session_factory):
    _, session = mock_session_factory
    
    with patch("server.storage.filesystem_bridge.StorageService.list_files", new_callable=AsyncMock, side_effect=lambda s, parent_id: MOCK_FILES[parent_id]), \
         patch("server.storage.filesystem_bridge.StorageService.get_file", new_callable=AsyncMock) as mock_get_file:
        
        mock_real_file = AsyncMock()
        mock_real_file.id = "file-1"
        mock_real_file.name = "students.csv"
        mock_real_file.is_directory = False
        mock_real_file.size_bytes = 25
        mock_get_file.return_value = mock_real_file
        
        node = bridge._resolve_path("/数据/students.csv")
        assert node["id"] == "file-1"
        assert node["is_dir"] is False
        assert node["size"] == 25

def test_readdir(bridge, mock_session_factory):
    with patch("server.storage.filesystem_bridge.StorageService.list_files", new_callable=AsyncMock, side_effect=lambda s, parent_id: MOCK_FILES[parent_id]):
        files = bridge.readdir("/", fh=None)
        assert "数据" in files
        assert "." in files and ".." in files

def test_getattr_file(bridge, mock_session_factory):
    _, _ = mock_session_factory
    with patch.object(bridge, "_resolve_path", return_value={"is_dir": False, "size": 25}):
        attr = bridge.getattr("/students.csv")
        assert stat.S_ISREG(attr['st_mode'])
        assert attr['st_size'] == 25

def test_open_readonly(bridge):
    with pytest.raises(OSError) as exc_info:
        bridge.open("/students.csv", flags=1)
    assert exc_info.value.errno == errno.EROFS

def test_read_content(bridge, mock_session_factory):
    _, session = mock_session_factory
    
    with patch.object(bridge, "_resolve_path", return_value={"id": "file-1", "is_dir": False}), \
         patch("server.storage.filesystem_bridge.StorageService.download_path", new_callable=AsyncMock) as mock_download:
        
        with open("/tmp/mock.csv", "w") as f:
            f.write("id,name,score\n1,Alice,95")
            
        mock_download.return_value = (None, "/tmp/mock.csv")

        fh = bridge.open("/students.csv", flags=0)
        content = bridge.read("/students.csv", size=100, offset=0, fh=fh)
        assert b"Alice" in content
        bridge.release("/students.csv", fh)