from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


def _load_runtime_module():
    path = Path(__file__).parents[1] / "sandbox-runtime" / "nova_runtime.py"
    spec = importlib.util.spec_from_file_location("nova_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_protocol_clamps_stream_output_and_marks_it_truncated() -> None:
    runtime = _load_runtime_module()

    result = runtime.OutputCollector(limit_bytes=5)
    result.append("stdout", "abcdef")

    assert result.to_payload() == {"stdout": "abcde", "stderr": "", "truncated": True}


def test_protocol_rejects_an_invalid_execute_request() -> None:
    runtime = _load_runtime_module()

    try:
        runtime.parse_execute_request({"source": "", "timeout_seconds": 2, "output_limit_bytes": 10})
    except ValueError as error:
        assert "source" in str(error)
    else:
        raise AssertionError("empty source must be rejected")


def test_scratch_protocol_marks_user_code_errors_as_failed() -> None:
    runtime = _load_runtime_module()
    result = runtime.scratch(
        runtime.ExecuteRequest("raise RuntimeError('boom')", 5, 1024)
    )
    assert result["status"] == "failed"
    assert "RuntimeError" in result["stderr"]


def test_runtime_artifact_payload_rejects_path_escape() -> None:
    from server.sandbox.artifact_persistence import normalized_runtime_artifacts

    assert normalized_runtime_artifacts([
        {"name": "../secret.txt", "mime_type": "text/plain", "content_b64": "c2VjcmV0"},
        {"name": "report.txt", "mime_type": "text/plain", "content_b64": "b2s="},
    ]) == [("report.txt", "text/plain", b"ok")]


def test_scratch_protocol_enforces_its_own_deadline() -> None:
    if os.name == "nt":
        return
    runtime = _load_runtime_module()

    result = runtime.scratch(runtime.ExecuteRequest("while True: pass", 1, 1024))

    assert result["status"] == "failed"
    assert "TimeoutError" in result["stderr"]
