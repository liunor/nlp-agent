"""Per-session five-layer context governance for model-facing message views."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.session_context import (
    LocalContextStateRepository,
    PersistedContextState,
    SessionContext,
    local_context_repository,
)
from server.agent.compression.auto_compact import autocompact_if_needed
from server.agent.compression.context_collapse import CollapseStore, apply_collapses_if_needed
from server.agent.compression.internal_context import (
    COMPRESSION_KIND_KEY,
    is_internal_context_message,
)
from server.agent.compression.micro_compact import micro_compact_if_needed
from server.agent.compression.message_integrity import remove_orphaned_tool_messages
from utils.logger import get_logger
from utils.tokens import ContextBudget, rough_estimation_for_messages


logger = get_logger("nlp_agent.context_manager")


@dataclass
class ContextTransform:
    session: SessionContext
    messages: list[BaseMessage]
    tokens_before: int
    tokens_after: int
    context_window: int = 0
    input_limit: int = 0
    output_reserve: int = 0
    actions: list[str] = field(default_factory=list)
    removed_message_ids: list[str] = field(default_factory=list)


class ContextManager:
    def __init__(self, repository: LocalContextStateRepository = local_context_repository) -> None:
        self.repository = repository
        self._stores: dict[str, CollapseStore] = {}

    def _store(self, context: SessionContext, state: PersistedContextState) -> CollapseStore:
        store = self._stores.get(context.storage_key)
        if store is None:
            store = CollapseStore()
            store.load_commits(state.collapse_commits)
            self._stores[context.storage_key] = store
        return store

    async def prepare(
        self,
        context: SessionContext,
        messages: list[BaseMessage],
        budget: ContextBudget,
    ) -> ContextTransform:
        async with self.repository.lock_for(context):
            state = self.repository.load(context)
            store = self._store(context, state)
            original_messages = list(messages)
            before = rough_estimation_for_messages(messages)
            view = list(messages)
            actions: list[str] = []

            micro = micro_compact_if_needed(
                view,
                force=before >= budget.threshold(0.60),
            )
            if micro.tools_cleared:
                view = micro.messages
                actions.append(f"micro_compact:{micro.tools_cleared}")
                # One successful automatic layer owns this prepare call.
                # Only the hard overflow guard may run after it.
                return self._finish_transform(
                    context, original_messages, view, before, budget, actions
                )

            commit_count = len(store.commits)
            projected = await apply_collapses_if_needed(
                view,
                store,
                input_limit=budget.input_limit,
            )
            collapse_changed = _message_signature(projected) != _message_signature(view)
            view = projected
            if len(store.commits) > commit_count:
                new_commits = store.commits[commit_count:]
                actions.append(f"collapse:{len(new_commits)}")
                state = state.model_copy(
                    update={
                        "collapse_commits": [
                            {
                                "collapse_id": item.collapse_id,
                                "summary_uuid": item.summary_uuid,
                                "summary_content": item.summary_content,
                                "first_msg_uuid": item.first_msg_uuid,
                                "last_msg_uuid": item.last_msg_uuid,
                            }
                            for item in store.commits
                        ]
                    }
                )
                self.repository.save(context, state)
                from server.memory.runtime import global_memory_runtime

                for item in new_commits:
                    global_memory_runtime.archive_summary(
                        context,
                        source_id=f"collapse:{item.collapse_id}",
                        summary=item.summary_content,
                        source_message_ids=(item.first_msg_uuid, item.last_msg_uuid),
                    )

            if collapse_changed:
                # Context Collapse is a complete automatic layer for this
                # request. Do not immediately fall through to Layer 5.
                if not any(action.startswith("collapse:") for action in actions):
                    actions.append("collapse:projection")
                return self._finish_transform(
                    context, original_messages, view, before, budget, actions
                )

            compact = await autocompact_if_needed(
                view,
                threshold=budget.threshold(0.93),
                session_id=context.storage_key,
            )
            if compact.was_compacted:
                auto_removed = _removed_message_ids(view, compact.messages)
                view = compact.messages
                actions.append("auto_compact")
                if compact.summary:
                    from server.memory.runtime import global_memory_runtime

                    global_memory_runtime.archive_summary(
                        context,
                        source_id=f"auto_compact:{auto_removed[0] if auto_removed else context.session_id}",
                        summary=compact.summary,
                        source_message_ids=tuple(auto_removed),
                    )
                return self._finish_transform(
                    context, original_messages, view, before, budget, actions
                )

            return self._finish_transform(
                context, original_messages, view, before, budget, actions
            )

    def _finish_transform(
        self,
        context: SessionContext,
        original_messages: list[BaseMessage],
        view: list[BaseMessage],
        before: int,
        budget: ContextBudget,
        actions: list[str],
    ) -> ContextTransform:
        if rough_estimation_for_messages(view) > budget.input_limit:
            view = trim_legal_history(view, budget.input_limit)
            actions.append("hard_trim")
        else:
            view = remove_orphaned_tool_messages(view)

        return ContextTransform(
            session=context,
            messages=view,
            tokens_before=before,
            tokens_after=rough_estimation_for_messages(view),
            context_window=budget.context_window,
            input_limit=budget.input_limit,
            output_reserve=budget.output_reserve,
            actions=actions,
            removed_message_ids=_removed_message_ids(original_messages, view),
        )

    async def inspect(self, context: SessionContext) -> PersistedContextState:
        async with self.repository.lock_for(context):
            return self.repository.load(context)

    async def clear(self, context: SessionContext) -> None:
        async with self.repository.lock_for(context):
            self._stores.pop(context.storage_key, None)
            self.repository.delete(context)


def trim_legal_history(messages: list[BaseMessage], token_limit: int) -> list[BaseMessage]:
    """Keep system messages and newest complete user turns within a hard budget."""
    if rough_estimation_for_messages(messages) <= token_limit:
        return messages
    system_messages = [message for message in messages if isinstance(message, SystemMessage)]
    normal_systems = [
        message for message in system_messages if not is_internal_context_message(message)
    ]
    internal_systems = [
        message for message in system_messages if is_internal_context_message(message)
    ]
    conversation = [message for message in messages if not isinstance(message, SystemMessage)]
    turns: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    for message in conversation:
        if isinstance(message, HumanMessage) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)

    # A malformed or oversized injected system message must not make the
    # emergency guard return a view that still exceeds the provider limit.
    systems: list[BaseMessage] = []
    used = 0
    for message in normal_systems:
        cost = rough_estimation_for_messages([message])
        if used + cost <= token_limit:
            systems.append(message)
            used += cost

    selected_internal: list[BaseMessage] = []
    for message in sorted(
        internal_systems,
        key=_internal_context_priority,
        reverse=True,
    ):
        cost = rough_estimation_for_messages([message])
        if used + cost <= token_limit:
            selected_internal.append(message)
            used += cost

    kept: list[list[BaseMessage]] = []
    for turn in reversed(turns):
        legal = _legalize_turn(turn)
        cost = rough_estimation_for_messages(legal)
        if used + cost > token_limit:
            break
        kept.append(legal)
        used += cost
    kept.reverse()
    selected_system_object_ids = {
        id(message) for message in [*systems, *selected_internal]
    }
    result_systems = [
        message
        for message in system_messages
        if id(message) in selected_system_object_ids
    ]
    result = result_systems + [message for turn in kept for message in turn]
    logger.info(
        "Hard context trim completed",
        original=len(messages),
        kept=len(result),
        estimated_tokens=rough_estimation_for_messages(result),
    )
    return result


def _internal_context_priority(message: BaseMessage) -> tuple[int, int]:
    metadata = getattr(message, "additional_kwargs", {}) or {}
    kind = metadata.get(COMPRESSION_KIND_KEY)
    priority = {
        "auto_compact": 100,
        "collapse": 80,
        "restoration": 60,
        "snip_boundary": 20,
        "snip_marker": 10,
    }.get(kind, 50)
    return priority, 0


def _legalize_turn(turn: list[BaseMessage]) -> list[BaseMessage]:
    declared: set[str] = set()
    completed: set[str] = {
        message.tool_call_id for message in turn if isinstance(message, ToolMessage)
    }
    output: list[BaseMessage] = []
    for message in turn:
        if isinstance(message, AIMessage) and message.tool_calls:
            ids = {str(call.get("id")) for call in message.tool_calls if call.get("id")}
            if not ids.issubset(completed):
                continue
            declared.update(ids)
        if isinstance(message, ToolMessage) and message.tool_call_id not in declared:
            continue
        output.append(message)
    while output and isinstance(output[0], (AIMessage, ToolMessage)):
        output.pop(0)
    return output


global_context_manager = ContextManager()


def _message_signature(messages: list[BaseMessage]) -> tuple[tuple[str, str | None], ...]:
    return tuple(
        (message.__class__.__name__, str(message.id) if message.id is not None else None)
        for message in messages
    )


def _removed_message_ids(
    original_messages: list[BaseMessage], view: list[BaseMessage]
) -> list[str]:
    visible_ids = {str(message.id) for message in view if message.id is not None}
    return [
        str(message.id)
        for message in original_messages
        if message.id is not None and str(message.id) not in visible_ids
    ]
