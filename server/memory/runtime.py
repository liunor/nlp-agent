"""Coordinator-facing scoped memory lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.messages import SystemMessage

from configs.settings import settings
from core.session_context import SessionContext
from core.prompt_runtime import global_prompt_runtime
from server.memory.curator import MemoryCurator
from server.memory.manager import MEMORY_DIR, MemoryManager
from server.memory.mysql_manager import MySQLMemoryManager
from server.memory.types import MemoryRuntimeConfig
from utils.logger import get_logger


logger = get_logger("nlp_agent.memory.runtime")


class MemoryRuntime:
    def __init__(
        self,
        *,
        root: str | Path = MEMORY_DIR,
        config: MemoryRuntimeConfig | None = None,
        curator: MemoryCurator | None = None,
    ) -> None:
        self.root = Path(root)
        self.config = config or MemoryRuntimeConfig()
        self.curator = curator or MemoryCurator()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def manager(self, context: SessionContext) -> MySQLMemoryManager | MemoryManager:
        # A custom root is the explicit test/embedded-store seam retained by
        # the public runtime API. Production uses the durable MySQL store at
        # the configured default root; isolated roots must not require a
        # corresponding MySQL user/workspace fixture.
        if self.root.resolve() != Path(MEMORY_DIR).resolve():
            return MemoryManager(context, self.root)
        return MySQLMemoryManager(context)

    def context_message(self, context: SessionContext) -> SystemMessage | None:
        if not self.config.enabled or context.agent_id != "coordinator":
            return None
        manager = self.manager(context)
        if not manager.scan_memory_headers() and not manager.read_archives(
            since_cursor=manager.get_curator_cursor(),
            session_id=context.session_id,
        ):
            return None
        content = manager.build_injection_text(
            max_tokens=self.config.max_injection_tokens,
            max_topics=self.config.max_topics,
            recent_archive_tokens=self.config.recent_archive_tokens,
        )
        return SystemMessage(
            content=global_prompt_runtime.render("memory.inject", memory=content),
            additional_kwargs={"memory_scope": context.storage_key, "transient": True},
        )

    def archive_summary(
        self,
        context: SessionContext,
        *,
        source_id: str,
        summary: str,
        source_message_ids: tuple[str, ...] = (),
    ) -> None:
        if not self.config.enabled or context.agent_id != "coordinator":
            return
        manager = self.manager(context)
        manager.append_archive(
            source_id=source_id,
            summary=summary,
            source_message_ids=source_message_ids,
        )
        self._schedule_if_needed(context, manager)

    def _schedule_if_needed(
        self,
        context: SessionContext,
        manager: MySQLMemoryManager,
    ) -> None:
        pending = manager.read_archives(since_cursor=manager.get_curator_cursor())
        if len(pending) < self.config.curate_after_archives:
            return
        key = f"{context.workspace_id}\0{context.user_id}"
        current = self._tasks.get(key)
        if current is not None and not current.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tasks[key] = loop.create_task(self._curate(context, key))

    async def _curate(self, context: SessionContext, key: str) -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                from core.observability.context import current_telemetry_context
                from core.observability.models import SpanKind
                from core.observability.runtime import global_telemetry

                telemetry = current_telemetry_context()
                if telemetry is None:
                    applied = await self.curator.curate(context, self.manager(context))
                else:
                    async with global_telemetry.span(
                        SpanKind.MEMORY, "memory.curate", context=telemetry,
                        attributes={"memory_scope": key},
                    ) as span:
                        applied = await self.curator.curate(context, self.manager(context))
                        span.annotate(applied=applied)
                logger.info("Memory curation completed", scope=key, applied=applied)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Memory curation failed", scope=key, error=str(error))

    async def curate_now(self, context: SessionContext) -> int:
        key = f"{context.workspace_id}\0{context.user_id}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self.curator.curate(context, self.manager(context))

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


global_memory_runtime = MemoryRuntime(
    config=MemoryRuntimeConfig.model_validate(settings.memory_runtime)
)
