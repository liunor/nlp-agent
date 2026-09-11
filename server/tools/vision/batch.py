"""Collect completed image tools before the Worker delivery deadline."""

from __future__ import annotations

import asyncio

from core.tool_runtime import ToolExecutionError, ToolExecutionResult, ToolSet


async def execute_image_batch(
    toolset: ToolSet, calls: list[tuple[str, dict]], config: dict, *, timeout_s: float
) -> list[ToolExecutionResult]:
    tasks = [asyncio.create_task(toolset.execute(name, args, config)) for name, args in calls]
    try:
        done, _ = await asyncio.wait(tasks, timeout=max(0, timeout_s))
        return [
            task.result() if task in done else ToolExecutionResult(
                tool_name=name,
                ok=False,
                attempts=0,
                error=ToolExecutionError(
                    kind="timeout", code="image_batch_deadline", retryable=False,
                    message="本图达到批量处理时限，已保留其他完成图片的结果；本轮不要重试。",
                ),
            )
            for (name, _), task in zip(calls, tasks, strict=True)
        ]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
