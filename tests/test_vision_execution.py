import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from core.agent_runtime import configured_budget
from core.tool_runtime import ToolDescriptor, ToolGrantRequest, ToolRuntime, ToolScope, ToolSource
from core.vision_execution import attached_image_names, bind_image_turn, current_image_turn, image_turn_timeout
from server.tools.vision.batch import execute_image_batch
from server.tools.worker_tool import _aligned_worker_timeouts


def attachment_content(count):
    return "识别图片\n\n---附件---\n" + "\n".join(
        f"[图片] image-{i}.png\n路径: image-{i}.png" for i in range(count)
    ) + "\n---附件结束---"


@pytest.mark.parametrize("count,expected", [(0, 120), (1, 180), (2, 180), (4, 270), (5, 360), (100, 360)])
def test_deadline_accounts_for_batches_with_a_finite_cap(count, expected):
    names = attached_image_names(attachment_content(count))
    assert len(names) == min(count, 5)
    assert image_turn_timeout(120, len(names)) == expected


def test_plain_text_and_incomplete_attachment_blocks_do_not_expand_budget():
    assert attached_image_names("recognize image.png") == ()
    assert attached_image_names("---附件---\n[图片] image.png\n路径: another.png\n---附件结束---") == ()


def test_worker_and_coordinator_share_batched_deadline_without_leaking_context():
    descriptor = ToolDescriptor(
        name="image_analyze", description="test", source=ToolSource.BUILTIN,
        scopes=frozenset({ToolScope.WORKER}), timeout_s=90, max_concurrency=2,
        factory=lambda: None,
    )
    toolset = SimpleNamespace(descriptors=(descriptor,), has=lambda name: name == "image_analyze")
    with bind_image_turn(4, 270):
        duration, wait = _aligned_worker_timeouts(
            toolset, max_duration_s=120, wait_timeout_s=120, join=True, expected_image_calls=4,
        )
        assert duration == 210 and wait == 215
        assert configured_budget("coordinator").max_duration_s == 270
        short_duration, short_wait = _aligned_worker_timeouts(
            toolset, max_duration_s=600, wait_timeout_s=600, join=True, expected_image_calls=4,
        )
        assert short_duration < short_wait < 240
    assert current_image_turn() is None
    assert configured_budget("coordinator").max_duration_s == 120


def image_toolset(invoke):
    runtime = ToolRuntime()
    runtime.catalog.register(ToolDescriptor(
        name="image_analyze", description="test image", source=ToolSource.BUILTIN,
        scopes=frozenset({ToolScope.WORKER}), read_only=True,
        timeout_s=1, max_concurrency=2, concurrency_safe=True,
        factory=lambda: StructuredTool.from_function(
            coroutine=invoke, name="image_analyze", description="test image",
        ),
    ))
    return runtime.build_toolset(ToolGrantRequest(role=ToolScope.WORKER, allowed_tools={"image_analyze"}))


async def test_four_images_share_two_slots_and_finish_both_batches():
    active = peak = 0
    async def invoke(image: str):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            return image
        finally:
            active -= 1
    results = await execute_image_batch(
        image_toolset(invoke), [("image_analyze", {"image": str(i)}) for i in range(4)], {}, timeout_s=2,
    )
    assert peak == 2 and active == 0
    assert all(result.ok for result in results)
    assert [result.output for result in results] == [str(i) for i in range(4)]


async def test_batch_deadline_preserves_completed_image_and_cancels_slow_one():
    cancelled = asyncio.Event()
    async def invoke(image: str):
        if image == "fast":
            return "verified image description"
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    results = await execute_image_batch(
        image_toolset(invoke), [("image_analyze", {"image": name}) for name in ("fast", "slow")], {}, timeout_s=0.05,
    )
    assert results[0].ok and results[0].output == "verified image description"
    assert results[1].error.code == "image_batch_deadline"
    assert not results[1].error.retryable and cancelled.is_set()


async def test_parent_cancellation_propagates_and_drains_image_tasks():
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def invoke(image: str):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    task = asyncio.create_task(execute_image_batch(
        image_toolset(invoke), [("image_analyze", {"image": "slow"})], {}, timeout_s=2,
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.parametrize("terminal", ["provider_quota_exhausted", "model_error", "model_timeout"])
async def test_worker_delivers_image_evidence_when_provider_or_summary_fails(monkeypatch, terminal):
    from langchain_core.messages import AIMessage
    from core.worker_lifecycle import WorkerResourceBudget
    from server.tools import worker_tool

    invoked = []
    async def invoke(image: str):
        invoked.append(image)
        if image == "blocked":
            return '{"error":"provider quota exhausted","code":"provider_quota_exhausted"}'
        return "verified image description"

    class Model:
        calls = 0
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[
                    {"name": "image_analyze", "args": {"image": name}, "id": name}
                    for name in (["good", "blocked"] if terminal == "provider_quota_exhausted" else ["good"])
                ])
            if terminal == "model_timeout":
                await asyncio.Event().wait()
            raise RuntimeError("summary model failed")

    async def prepare(context, messages, budget):
        return SimpleNamespace(messages=messages, tokens_before=0, tokens_after=0, actions=[])

    model = Model()
    monkeypatch.setattr(worker_tool, "get_tool_llm", lambda: model)
    monkeypatch.setattr(worker_tool, "global_context_manager", SimpleNamespace(prepare=prepare))
    monkeypatch.setattr(worker_tool, "record_sidechain_transcript", lambda *args: None)
    # Keep the test fast while exercising the actual Worker timeout path.
    real_batch = execute_image_batch
    async def quick_batch(toolset, calls, config, *, timeout_s):
        return await real_batch(toolset, calls, config, timeout_s=1)
    monkeypatch.setattr("server.tools.vision.batch.execute_image_batch", quick_batch)
    result = await worker_tool._execute_sandbox_loop(
        "image-worker", "image-session", [], image_toolset(invoke),
        budget=WorkerResourceBudget(max_duration_s=0.15 if terminal == "model_timeout" else 2),
    )
    assert "verified image description" in result.output
    assert not result.error.retryable
    assert invoked.count("good") == 1
    if terminal == "provider_quota_exhausted":
        assert model.calls == 1
        assert "provider_quota_exhausted" in result.output
