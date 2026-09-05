from fastapi import HTTPException

from server.rbac.service import LastDeveloperForbiddenError, UnknownRoleError
from server.web.app import _rbac_http_error


def test_rbac_domain_errors_are_exposed_as_stable_http_errors():
    cases = [
        (KeyError("missing"), 404, "RBAC resource not found"),
        (UnknownRoleError("unknown role"), 400, "unknown role"),
        (LastDeveloperForbiddenError("last developer"), 409, "last developer"),
        (PermissionError("forbidden"), 403, "forbidden"),
        (ValueError("invalid"), 422, "invalid"),
    ]
    for error, expected_status, expected_detail in cases:
        translated = _rbac_http_error(error)
        assert isinstance(translated, HTTPException)
        assert translated.status_code == expected_status
        assert translated.detail == expected_detail
