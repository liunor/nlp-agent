"""Low-frequency memory curation from trusted session archive summaries."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from core.session_context import SessionContext
from core.prompt_runtime import global_prompt_runtime
from server.agent.llm_factory import get_utility_llm
from server.memory.manager import MemoryManager
from server.memory.types import MemoryCurationResult, MemoryScopeKind
from utils.logger import get_logger


get_tool_llm = get_utility_llm  # Backward-compatible injection seam for hosts/tests.
logger = get_logger("nlp_agent.memory.curator")


class MemoryCurator:
    """Turn append-only archives into small, durable Markdown topics."""

    async def curate(self, context: SessionContext, manager: MemoryManager) -> int:
        cursor = manager.get_curator_cursor()
        archives = manager.read_archives(since_cursor=cursor)
        if not archives:
            return 0

        batch = archives[:50]
        allowed_evidence = {row.archive_id for row in batch}
        archive_text = "\n\n".join(
            f"ARCHIVE {row.archive_id} session={row.session_id}\n{row.summary}"
            for row in batch
        )
        system = SystemMessage(content=global_prompt_runtime.render("memory.curator"))
        prompt = HumanMessage(
            content=global_prompt_runtime.render(
                "memory.curate_request",
                memory=manager.build_injection_text(
                    max_tokens=12_000, max_topics=30, recent_archive_tokens=0
                ),
                archives=archive_text,
            )
        )
        from core.model_runtime.usage import bind_usage_purpose

        with bind_usage_purpose("memory"):
            result = await get_tool_llm().with_structured_output(
                MemoryCurationResult
            ).ainvoke([system, prompt])

        applied = 0
        for operation in result.operations if result else []:
            evidence = set(operation.evidence_archive_ids)
            if operation.operation in {"ignore", "delete"}:
                continue
            if (
                operation.confidence < 0.8
                or not evidence
                or not evidence.issubset(allowed_evidence)
            ):
                continue
            expected_scope = (
                MemoryScopeKind.WORKSPACE
                if operation.memory_type in {"project", "decision", "goal"}
                else MemoryScopeKind.USER
            )
            if operation.scope != expected_scope:
                continue
            try:
                manager.apply_curator_operation(operation)
                applied += 1
            except Exception as error:
                logger.warning(
                    "Memory curator operation rejected",
                    filename=operation.filename,
                    error=str(error),
                )

        # A clean curator response consumes the batch even when it intentionally
        # produces no durable memory. Failed LLM calls never reach this point.
        manager.set_curator_cursor(batch[-1].cursor)
        return applied
