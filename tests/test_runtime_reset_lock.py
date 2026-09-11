from unittest.mock import MagicMock

from core.observability.reset_lock import (
    RUNTIME_WRITE_LOCK_TIMEOUT_S,
    runtime_write_guard,
    runtime_write_transaction,
)


def test_runtime_writer_waits_for_the_global_reset_fence_by_default() -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.return_value = 1
    connection.begin.return_value.__enter__.return_value = connection

    with runtime_write_transaction(engine) as transaction:
        assert transaction is connection

    assert connection.scalar.call_args.args[1] == {
        "lock_name": "nlp_agent_monitor_runtime_reset",
        "timeout_s": RUNTIME_WRITE_LOCK_TIMEOUT_S,
    }


def test_runtime_write_guard_uses_the_same_bounded_wait() -> None:
    class Repository:
        _runtime_lock_enabled = True

        def __init__(self) -> None:
            self._engine = MagicMock()
            connection = self._engine.connect.return_value.__enter__.return_value
            connection.scalar.return_value = 1

        @runtime_write_guard
        def mutate(self) -> str:
            return "written"

    repository = Repository()

    assert repository.mutate() == "written"
    connection = repository._engine.connect.return_value.__enter__.return_value
    assert connection.scalar.call_args.args[1]["timeout_s"] == (
        RUNTIME_WRITE_LOCK_TIMEOUT_S
    )
