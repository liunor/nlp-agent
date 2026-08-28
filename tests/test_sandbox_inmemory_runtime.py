from __future__ import annotations


async def test_inmemory_runtime_keeps_variables_for_the_same_user_kernel() -> None:
    from server.sandbox.inmemory_runtime import InMemoryRuntime

    runtime = InMemoryRuntime()
    await runtime.execute(user_id="user-a", source="x = 1")
    result = await runtime.execute(user_id="user-a", source="x + 1")

    assert result["stdout"] == "2\n"


async def test_inmemory_runtime_does_not_share_variables_between_users() -> None:
    from server.sandbox.inmemory_runtime import InMemoryRuntime

    runtime = InMemoryRuntime()
    await runtime.execute(user_id="user-a", source="x = 1")
    result = await runtime.execute(user_id="user-b", source="'x' in globals()")

    assert result["stdout"] == "False\n"
