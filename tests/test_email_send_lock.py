from types import SimpleNamespace

import pytest

from server.auth import code_store


class _MysqlLockSession:
    def __init__(self):
        self.statements = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

    async def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))


@pytest.mark.asyncio
async def test_email_send_lock_uses_a_transaction_row_lock(monkeypatch):
    session = _MysqlLockSession()

    async with code_store.email_send_lock(session, "user@example.com"):
        pass

    sql = " ".join(statement for statement, _ in session.statements).lower()
    assert "nlp_email_send_locks" in sql
    assert "on duplicate key update" in sql
    assert "for update" in sql
