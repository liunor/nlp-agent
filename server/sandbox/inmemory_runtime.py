"""Development-only Phase 1 runtime; never deploy for untrusted code."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io


class InMemoryRuntime:
    """One in-process Python namespace per user, for Workbench contract tests."""

    def __init__(self) -> None:
        self._kernels: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def execute(self, *, user_id: str, source: str) -> dict[str, str]:
        namespace = self._kernels.setdefault(user_id, {"__builtins__": __builtins__})
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            stdout = io.StringIO()
            try:
                tree = ast.parse(source, mode="exec")
                with contextlib.redirect_stdout(stdout):
                    if tree.body and isinstance(tree.body[-1], ast.Expr):
                        prefix = ast.Module(body=tree.body[:-1], type_ignores=[])
                        exec(compile(prefix, "<sandbox>", "exec"), namespace, namespace)
                        value = eval(compile(ast.Expression(tree.body[-1].value), "<sandbox>", "eval"), namespace, namespace)
                        if value is not None:
                            print(repr(value))
                    else:
                        exec(compile(tree, "<sandbox>", "exec"), namespace, namespace)
                return {"stdout": stdout.getvalue(), "stderr": "", "status": "completed"}
            except Exception as error:
                return {"stdout": stdout.getvalue(), "stderr": f"{type(error).__name__}: {error}\n", "status": "error"}

    async def restart(self, *, user_id: str) -> None:
        self._kernels.pop(user_id, None)
