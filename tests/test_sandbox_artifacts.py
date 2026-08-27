from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

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


def test_malformed_artifact_ticket_is_always_a_permission_error() -> None:
    from server.sandbox.artifacts import ArtifactAccessSigner

    signer = ArtifactAccessSigner("test-secret")
    for ticket in ("not-a-ticket", "!!!!.!!!!", "eyJ4IjoxfQ.bad"):
        with pytest.raises(PermissionError):
            signer.verify(ticket, artifact_id="artifact-a", owner_user_id="user-a")


def test_artifact_expiry_normalizes_mysql_naive_utc_values() -> None:
    from datetime import datetime, timezone
    from server.sandbox.artifacts import artifact_expired

    assert artifact_expired(datetime(2020, 1, 1), now=datetime(2020, 1, 2, tzinfo=timezone.utc))
    assert not artifact_expired(None)


def test_artifact_access_url_uses_configured_separate_origin() -> None:
    from server.sandbox.artifacts import artifact_access_url

    assert artifact_access_url(
        "https://artifacts.example.test/",
        artifact_id="artifact-a",
        ticket="signed-ticket",
    ) == "https://artifacts.example.test/api/v1/sandbox/artifacts/artifact-a/content?ticket=signed-ticket"


def test_artifact_access_url_rejects_non_origin_https_values() -> None:
    from server.sandbox.artifacts import artifact_access_url

    with pytest.raises(ValueError):
        artifact_access_url("http://artifacts.example.test", artifact_id="a", ticket="t")
    with pytest.raises(ValueError):
        artifact_access_url("https://artifacts.example.test/path", artifact_id="a", ticket="t")


def test_artifact_origin_rejects_the_application_origin() -> None:
    from server.sandbox.artifacts import validate_artifact_origin

    with pytest.raises(ValueError, match="must differ"):
        validate_artifact_origin(
            "https://nova.example.test",
            application_origin="https://nova.example.test",
        )


def test_artifact_origin_rejects_same_hostname_with_case_or_trailing_dot() -> None:
    from server.sandbox.artifacts import validate_artifact_origin

    with pytest.raises(ValueError, match="must differ"):
        validate_artifact_origin(
            "https://ARTIFACTS.example.test",
            application_origin="https://artifacts.example.test.",
        )


def test_artifact_origin_rejects_a_path_component() -> None:
    from server.sandbox.artifacts import validate_artifact_origin

    with pytest.raises(ValueError, match="must not include a path"):
        validate_artifact_origin(
            "https://artifacts.example.test/nova",
            application_origin="https://nova.example.test",
        )


def test_artifact_locator_cannot_escape_configured_store(tmp_path: Path) -> None:
    from server.sandbox.artifacts import resolve_artifact_path

    artifact = tmp_path / "owned" / "output.html"
    artifact.parent.mkdir()
    artifact.write_text("<h1>safe</h1>", encoding="utf-8")

    assert resolve_artifact_path(tmp_path, "owned/output.html") == artifact
    with pytest.raises(PermissionError):
        resolve_artifact_path(tmp_path, "../secret.txt")


def test_artifact_response_verifies_ticket_and_adds_html_isolation_headers(tmp_path: Path) -> None:
    from server.sandbox.artifacts import ArtifactAccessSigner
    from server.sandbox.artifact_delivery import build_artifact_response

    artifact = tmp_path / "owned" / "output.html"
    artifact.parent.mkdir()
    artifact.write_text("<h1>safe</h1>", encoding="utf-8")
    metadata = SimpleNamespace(
        id="artifact-a",
        owner_user_id="user-a",
        locator="owned/output.html",
        mime_type="text/html",
    )
    signer = ArtifactAccessSigner("test-secret")
    ticket = signer.issue(artifact_id="artifact-a", owner_user_id="user-a")

    response = build_artifact_response(
        metadata,
        ticket=ticket,
        signer=signer,
        store_root=tmp_path,
    )

    assert response.media_type == "text/html"
    assert "sandbox" in response.headers["content-security-policy"]


def test_artifact_response_rechecks_metadata_ttl(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from server.sandbox.artifacts import ArtifactAccessSigner
    from server.sandbox.artifact_delivery import build_artifact_response

    artifact = tmp_path / "output.html"
    artifact.write_text("<h1>expired</h1>", encoding="utf-8")
    metadata = SimpleNamespace(
        id="artifact-expired",
        owner_user_id="user-a",
        locator="output.html",
        mime_type="text/html",
        expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    ticket = ArtifactAccessSigner("test-secret").issue(
        artifact_id="artifact-expired", owner_user_id="user-a"
    )
    with pytest.raises(PermissionError, match="expired"):
        build_artifact_response(
            metadata,
            ticket=ticket,
            signer=ArtifactAccessSigner("test-secret"),
            store_root=tmp_path,
        )


def test_artifact_response_allows_only_configured_application_frame(tmp_path: Path) -> None:
    from server.sandbox.artifacts import ArtifactAccessSigner
    from server.sandbox.artifact_delivery import build_artifact_response

    artifact = tmp_path / "output.html"
    artifact.write_text("<h1>safe</h1>", encoding="utf-8")
    metadata = SimpleNamespace(id="artifact-a", owner_user_id="user-a", locator="output.html", mime_type="text/html")
    signer = ArtifactAccessSigner("test-secret")
    ticket = signer.issue(artifact_id="artifact-a", owner_user_id="user-a")
    response = build_artifact_response(
        metadata,
        ticket=ticket,
        signer=signer,
        store_root=tmp_path,
        application_origin="https://nova.example.test",
    )

    assert "frame-ancestors https://nova.example.test" in response.headers["content-security-policy"]


def test_artifact_access_url_rejects_a_non_owner() -> None:
    from server.sandbox.artifact_delivery import issue_artifact_access_url
    from server.sandbox.artifacts import ArtifactAccessSigner

    metadata = SimpleNamespace(id="artifact-a", owner_user_id="user-a")
    with pytest.raises(PermissionError):
        issue_artifact_access_url(
            metadata,
            requester_user_id="user-b",
            signer=ArtifactAccessSigner("test-secret"),
            artifact_origin="https://artifacts.example.test",
            application_origin="https://nova.example.test",
        )
