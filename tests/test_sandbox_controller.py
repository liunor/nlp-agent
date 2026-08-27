from __future__ import annotations


def test_execution_output_preserves_stdout_and_stderr_streams() -> None:
    from server.sandbox.execution_events import execution_output_streams

    assert execution_output_streams({"stdout": "out\n", "stderr": "err\n"}) == (
        ("stdout", "out\n"),
        ("stderr", "err\n"),
    )


def test_execution_failure_payload_is_structured_and_bounded() -> None:
    from server.sandbox.execution_events import execution_failure_payload

    payload = execution_failure_payload(RuntimeError("secret details"))
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "RuntimeError: secret details"
    assert len(payload["error"]) <= 128


def test_runtime_result_status_marks_kernel_errors_as_failed() -> None:
    from server.sandbox.execution_events import execution_result_failure_payload, execution_result_failed

    result = {"status": "failed", "stdout": "partial", "stderr": "NameError: x"}
    assert execution_result_failed(result)
    assert execution_result_failure_payload(result) == {
        "error_type": "SandboxExecutionError",
        "error": "NameError: x",
    }
