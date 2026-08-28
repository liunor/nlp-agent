import asyncio
import time

import pytest
from unittest.mock import Mock

from core.task_manager import TaskManager
from core.worker_lifecycle import (
    InvalidWorkerTransition,
    WorkerResourceBudget,
    WorkerRetryPolicy,
    classify_worker_error,
)


@pytest.mark.asyncio
async def test_lifecycle_rejects_illegal_transition_and_records_timeline():
    manager = TaskManager()
    future = asyncio.create_task(asyncio.sleep(10))
    manager.register_task("w1", "research", "task", future, "s1")

    with pytest.raises(InvalidWorkerTransition, match="pending -> completed"):
        manager.transition_task("w1", "completed", "skipped_execution")

    manager.transition_task("w1", "running", "started")
    manager.complete_task("w1", "completed")
    assert manager.task_snapshot("w1")["status"] == "completed"
    assert [entry["reason"] for entry in manager.task_timeline("w1")][:2] == [
        "registered",
        "started",
    ]
    future.cancel()
    await asyncio.gather(future, return_exceptions=True)


@pytest.mark.asyncio
async def test_capacity_limits_keep_excess_worker_pending():
    manager = TaskManager(max_concurrent_workers=1, max_concurrent_per_session=1)
    first_future = asyncio.create_task(asyncio.sleep(10))
    second_future = asyncio.create_task(asyncio.sleep(10))
    manager.register_task("w1", "research", "one", first_future, "s1")
    manager.register_task("w2", "research", "two", second_future, "s1")

    await manager.acquire_execution_slot("w1")
    second_acquire = asyncio.create_task(manager.acquire_execution_slot("w2"))
    await asyncio.sleep(0)
    assert manager.get_active_task("w2").status == "pending"
    assert manager.metrics_snapshot()["capacity_waits"] == 1

    manager.release_execution_slot("w1")
    await asyncio.wait_for(second_acquire, 1)
    assert manager.get_active_task("w2").status == "running"
    assert manager.metrics_snapshot()["running_workers"] == 1
    manager.release_execution_slot("w2")
    first_future.cancel()
    second_future.cancel()
    await asyncio.gather(first_future, second_future, return_exceptions=True)


@pytest.mark.asyncio
async def test_parent_cancellation_cascades_to_children():
    manager = TaskManager()
    parent_future = asyncio.create_task(asyncio.sleep(10))
    child_future = asyncio.create_task(asyncio.sleep(10))
    manager.register_task("parent", "research", "parent", parent_future, "s1")
    manager.register_task(
        "child",
        "research",
        "child",
        child_future,
        "s1",
        parent_worker_id="parent",
    )
    manager.transition_task("parent", "running", "started")
    manager.transition_task("child", "running", "started")

    manager.stop_task("parent", reason="user_cancelled")
    assert manager.get_active_task("parent").status == "cancelling"
    assert manager.get_active_task("child").status == "cancelling"
    assert manager.get_active_task("child").cancellation_reason == "parent_cancelled:parent"
    await asyncio.gather(parent_future, child_future, return_exceptions=True)


def test_budget_retry_policy_and_failure_classification():
    budget = WorkerResourceBudget(max_turns=2, max_duration_s=1, max_tokens=10, max_tool_calls=0)
    policy = WorkerRetryPolicy(max_attempts=3, base_delay_s=0.25, max_delay_s=1)

    assert budget.max_tool_calls == 0
    assert policy.delay_for(1) == 0.25
    assert policy.delay_for(3) == 1
    assert policy.should_retry("timeout", 2) is True
    assert policy.should_retry("context", 1) is False
    assert classify_worker_error(TimeoutError("slow")) == ("timeout", True)
    assert classify_worker_error(RuntimeError("maximum context length")) == ("context", False)


def test_worker_configures_model_sandbox_tools_with_its_database_and_manager(monkeypatch) -> None:
    monkeypatch.setenv(
        "NLP_AGENT_DATABASE_URL",
        "mysql+aiomysql://nlp_agent:nlp_agent_dev@127.0.0.1:3306/nlp_agent?charset=utf8mb4",
    )
    monkeypatch.setenv("NLP_AGENT_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("NLP_AGENT_WEB_SECRET", "worker-sandbox-test-secret")
    import server.worker.runtime as worker_runtime
    from configs.settings import settings

    manager = object()
    service = object()
    configured = Mock(return_value=service)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_RUNTIME_MODE", "docker")
    monkeypatch.setattr(worker_runtime, "create_sandbox_manager_rpc_client", lambda: manager, raising=False)
    monkeypatch.setattr(worker_runtime, "configure_model_sandbox_service", configured, raising=False)

    result = worker_runtime.configure_worker_sandbox_service("database-session-factory")

    assert result == (service, manager)
    configured.assert_called_once_with(
        mode="docker",
        session_factory="database-session-factory",
        manager=manager,
    )
