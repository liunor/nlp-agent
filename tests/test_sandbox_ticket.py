from __future__ import annotations


def test_ticket_is_bound_to_session_and_can_be_renewed_without_nonce() -> None:
    from server.sandbox.ticket import SandboxTicketClaims, SandboxTicketSigner

    signer = SandboxTicketSigner("test-secret")
    token = signer.issue(SandboxTicketClaims("user-a", "session-a", "lease-a", "runtime-a", 3, "nonce"))
    claims = signer.verify(token, user_id="user-a", auth_session_id="session-a")

    assert claims.nonce == "nonce"
    renewed = signer.issue(claims.without_nonce())
    assert signer.verify(renewed, user_id="user-a", auth_session_id="session-a").nonce is None


def test_ticket_rejects_a_different_authenticated_session() -> None:
    from server.sandbox.ticket import SandboxTicketClaims, SandboxTicketSigner

    signer = SandboxTicketSigner("test-secret")
    token = signer.issue(SandboxTicketClaims("user-a", "session-a", "lease-a", "runtime-a", 3, None))

    try:
        signer.verify(token, user_id="user-a", auth_session_id="session-b")
    except PermissionError:
        pass
    else:
        raise AssertionError("ticket must be session-bound")
