"""Identity dependencies package."""

from server.web.dependencies.identity import (
    get_workspace_principal,
    get_db_session,
)

__all__ = ["get_workspace_principal", "get_db_session"]
