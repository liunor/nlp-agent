import asyncio
import json

import pytest

from core.task_manager import TaskManager, global_task_manager
from server.tools.task_stop_tool import task_stop_tool


@pytest.mark.asyncio
async def test_task_stop_rejects_cross_session(monkeypatch):
    manager = TaskManager()
    future = asyncio.create_task(asyncio.sleep(10))
    manager.register_task("worker-private", "research", "secret", future, "session-a")
    manager.transition_task("worker-private", "running", "started")
    monkeypatch.setattr(
        "server.tools.task_stop_tool.global_task_manager", manager
    )

    raw = await task_stop_tool.ainvoke(
        {"task_id": "worker-private"},
        config={"configurable": {"thread_id": "session-b"}},
    )
    result = json.loads(raw)
    assert result["success"] is False
    assert result["error_code"] == 403
    assert manager.get_active_task("worker-private") is not None

    future.cancel()
    await asyncio.gather(future, return_exceptions=True)
#