from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError

from server.infrastructure.mysql.errors import database_problem


def test_pool_timeout_is_classified_as_retryable_database_busy() -> None:
    error = SQLAlchemyTimeoutError("QueuePool limit reached")

    assert database_problem(error) == (
        "database_busy",
        "Database connection pool is busy",
        "1",
    )


def test_mysql_lock_wait_and_deadlock_are_database_timeouts() -> None:
    for error_number, message in (
        (1205, "Lock wait timeout exceeded"),
        (1213, "Deadlock found when trying to get lock"),
        (3024, "Query execution was interrupted, maximum statement execution time exceeded"),
    ):
        error = OperationalError("UPDATE", {}, Exception(error_number, message))
        assert database_problem(error) == (
            "database_timeout",
            "Database operation timed out",
            "1",
        )


def test_other_database_failures_never_become_authentication_errors() -> None:
    error = OperationalError("SELECT 1", {}, Exception(2003, "connection refused"))

    assert database_problem(error)[0] == "database_unavailable"
