from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest


def test_untrusted_artifact_response_has_isolated_csp() -> None:
    from server.sandbox.artifacts import artifact_security_headers

    headers = artifact_security_headers("text/html")
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "sandbox" in headers["Content-Security-Policy"]
    assert headers["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_artifact_access_ticket_is_bound_to_owner_and_artifact() -> None:
    from server.sandbox.artifacts import ArtifactAccessSigner

    signer = ArtifactAccessSigner("test-secret")
    ticket = signer.issue(artifact_id="artifact-a", owner_user_id="user-a")

    signer.verify(ticket, artifact_id="artifact-a", owner_user_id="user-a")
    with pytest.raises(PermissionError):
        signer.verify(ticket, artifact_id="artifact-a", owner_user_id="user-b")


def test_artifact_access_ticket_expires() -> None:
    from server.sandbox.artifacts import ArtifactAccessSigner

    signer = ArtifactAccessSigner("test-secret", lifetime=timedelta(seconds=-1))
    ticket = signer.issue(artifact_id="artifact-a", owner_user_id="user-a")

    with pytest.raises(PermissionError, match="expired"):
        signer.verify(ticket, artifact_id="artifact-a", owner_user_id="user-a")


def test_artifact_access_url_uses_configured_separate_origin() -> None:
    from server.sandbox.artifacts import artifact_access_url

    assert artifact_access_url(
        "https://artifacts.example.test/",
        artifact_id="artifact-a",
        ticket="signed-ticket",
    ) == "https://artifacts.example.test/api/v1/sandbox/artifacts/artifact-a/content?ticket=signed-ticket"


def test_artifact_locator_cannot_escape_configured_store(tmp_path: Path) -> None:
    from server.sandbox.artifacts import resolve_artifact_path

    artifact = tmp_path / "owned" / "output.html"
    artifact.parent.mkdir()
    artifact.write_text("<h1>safe</h1>", encoding="utf-8")

    assert resolve_artifact_path(tmp_path, "owned/output.html") == artifact
    with pytest.raises(PermissionError):
        resolve_artifact_path(tmp_path, "../secret.txt")
