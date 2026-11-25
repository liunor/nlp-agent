import os
from pathlib import Path
import subprocess

import pytest

from server.tools.api import file_read_tool


def test_read_local_file_rejects_symlink_that_escapes_allowed_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "outside-secret.txt"
    secret.write_text("outside secret", encoding="utf-8")
    link = allowed / "linked-outside"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(file_read_tool, "ALLOWED_BASE", str(allowed))

    result = file_read_tool.read_local_file.invoke(
        {"file_path": str(link / "outside-secret.txt")}
    )

    assert result.startswith("安全限制：")
    assert "outside secret" not in result


def test_reader_only_reads_the_current_conversation_tool_results(tmp_path, monkeypatch):
    monkeypatch.setattr(file_read_tool, 'ALLOWED_BASE', str(tmp_path))
    own = tmp_path / 'chat_history' / 'own' / 'tool-results'
    other = tmp_path / 'chat_history' / 'other' / 'tool-results'
    own.mkdir(parents=True)
    other.mkdir(parents=True)
    (own / 'result.txt').write_text('own output', encoding='utf-8')
    (other / 'result.txt').write_text('private output', encoding='utf-8')
    config = {'configurable': {'thread_id': 'own', 'user_id': 'u', 'workspace_id': 'w'}}
    assert 'own output' in file_read_tool.read_local_file.invoke({'file_path': str(own / 'result.txt')}, config)
    denied = file_read_tool.read_local_file.invoke({'file_path': str(other / 'result.txt')}, config)
    assert denied.startswith('安全限制：') and 'private output' not in denied
