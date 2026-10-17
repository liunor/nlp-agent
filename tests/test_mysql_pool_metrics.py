from __future__ import annotations

import time
from threading import Event, Thread

import pytest
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from server.infrastructure.mysql.config import DatabaseConfig
from server.infrastructure.mysql.engine import create_engine as create_async_engine
from server.infrastructure.mysql.pool_metrics import MeasuredQueuePool
from server.infrastructure.mysql.pool_metrics import MeasuredAsyncAdaptedQueuePool
from server.infrastructure.mysql.sync_engine import configured_engine, release_engine


def test_pool_reports_connection_acquisition_latency_and_timeout_count() -> None:
    def slow_connection_factory():
        time.sleep(0.02)
        return object()

    pool = MeasuredQueuePool(
        creator=slow_connection_factory,
        pool_size=1,
        max_overflow=0,
        timeout=0.02,
    )
    try:
        connection = pool.connect()
        acquired = pool.checkout_metrics()
        assert acquired["count"] == 1
        assert acquired["sum_ms"] >= 10
        assert acquired["max_ms"] >= 10
        assert acquired["buckets_le_ms"]["25"] == 1

        with pytest.raises(SQLAlchemyTimeoutError):
            pool.connect()

        after_timeout = pool.checkout_metrics()
        assert after_timeout["timeouts"] == 1
        assert after_timeout["count"] == 2
    finally:
        if "connection" in locals():
            connection.close()
        pool.dispose()


def test_pool_exposes_the_number_of_pending_checkouts() -> None:
    class FakeConnection:
        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    pool = MeasuredQueuePool(
        creator=FakeConnection, pool_size=1, max_overflow=0, timeout=1
    )
    first = pool.connect()
    checkout_started = Event()
    second_checkout = []

    def acquire_second_connection() -> None:
        checkout_started.set()
        connection = pool.connect()
        second_checkout.append(connection)
        connection.close()

    thread = Thread(target=acquire_second_connection)
    thread.start()
    assert checkout_started.wait(1)
    deadline = time.monotonic() + 0.5
    while pool.checkout_metrics().get("pending") != 1 and time.monotonic() < deadline:
        time.sleep(0.001)
    try:
        assert pool.checkout_metrics()["pending"] == 1
    finally:
        first.close()
        thread.join(timeout=1)
        pool.dispose()

    assert not thread.is_alive()
    assert len(second_checkout) == 1


def test_mysql_engine_factories_install_measured_pool_variants() -> None:
    config = DatabaseConfig(url="mysql+aiomysql://user:pass@127.0.0.1/nlp_agent")
    async_engine = create_async_engine(config)
    assert isinstance(async_engine.sync_engine.pool, MeasuredAsyncAdaptedQueuePool)

    sync_engine = configured_engine(
        config.url,
        {
            "url": config.url,
            "sync_pool_size": 1,
            "sync_max_overflow": 0,
            "pool_timeout_s": 5,
        },
    )
    try:
        assert isinstance(sync_engine.pool, MeasuredQueuePool)
    finally:
        release_engine(sync_engine)
