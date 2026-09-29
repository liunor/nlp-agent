"""
Layer 4: Context Collapse (读时投影)

这是一个读时拦截器。当给 LLM 发送请求前调用此模块。
根据当前上下文的 Token 压力，进行 Stage(暂存) -> Commit(摘要并持久化) -> Blocking(紧急清空) 三段式管理。
最后，将消息列表中的被折叠区域，替换为 <collapsed> 标签，从而对大模型真正隐藏这部分 Token。
"""
import uuid
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage

from server.agent.compression.internal_context import (
    internal_context_metadata,
    invoke_internal_model,
)
from utils.tokens import rough_estimation_for_messages
from utils.logger import get_logger
from core.prompt_runtime import global_prompt_runtime

logger = get_logger("shiliu.context_collapse")

EFFECTIVE_WINDOW = 180_000     # 200K - 20K (预留给输出)
# Compatibility constants for direct callers/tests; ContextManager derives
# runtime thresholds from the selected model's input budget.
COMMIT_THRESHOLD = int(EFFECTIVE_WINDOW * 0.90)
AUTOCOMPACT_THRESHOLD = int(EFFECTIVE_WINDOW * 0.93)
BLOCKING_THRESHOLD = int(EFFECTIVE_WINDOW * 0.95)
KEEP_RECENT = 15

@dataclass
class CollapseCommit:
    collapse_id: str
    summary_uuid: str
    summary_content: str
    first_msg_uuid: str
    last_msg_uuid: str

@dataclass
class StagedSpan:
    start_uuid: str
    end_uuid: str
    summary: str = ""
    risk: float = 0.0
    staged_at: float = field(default_factory=time.time)

class CollapseStore:
    def __init__(self):
        self.commits: List[CollapseCommit] = []
        self.staged: List[StagedSpan] = []
        self.uuid_to_id: Dict[str, str] = {}
        self.enabled: bool = True
        
    def add_commit(self, commit: CollapseCommit):
        self.commits.append(commit)
        self.uuid_to_id[commit.summary_uuid] = commit.collapse_id

    def load_commits(self, commits_data: List[dict]):
        """从 JSONL 重建时加载"""
        self.commits.clear()
        self.uuid_to_id.clear()
        for d in commits_data:
            c = CollapseCommit(
                collapse_id=d["collapse_id"],
                summary_uuid=d["summary_uuid"],
                summary_content=d["summary_content"],
                first_msg_uuid=d["first_msg_uuid"],
                last_msg_uuid=d["last_msg_uuid"],
            )
            self.add_commit(c)

async def apply_collapses_if_needed(
    messages: List[BaseMessage],
    store: CollapseStore,
    *,
    input_limit: int = EFFECTIVE_WINDOW,
) -> List[BaseMessage]:
    """
    读时投影的主入口。如果需要，会执行 Stage, Commit, 甚至 Blocking 排空。
    最终返回 project_view（不修改原始 messages）。
    """
    if not store.enabled or not messages:
        return messages

    token_count = rough_estimation_for_messages(messages)
    
    commit_threshold = int(input_limit * 0.90)
    blocking_threshold = int(input_limit * 0.95)

    if token_count < commit_threshold:
        # Stage 阶段: 寻找新的可折叠区段
        _stage_spans(messages, store)
        return _project_view(messages, store)
        
    if token_count >= blocking_threshold:
        # Blocking 阶段: 紧急排空所有 staged，不管 risk
        await _commit_staged(messages, store, limit=None)
        return _project_view(messages, store)
        
    if token_count >= commit_threshold:
        # Commit 阶段: 每次最多生成 2 个区段的摘要，避免单次耗时过长
        await _commit_staged(messages, store, limit=2)
        return _project_view(messages, store)

    return messages


def _stage_spans(messages: List[BaseMessage], store: CollapseStore):
    """
    扫描较老的历史，将还没折叠的消息划分为 StagedSpan。
    为了简化，我们将连续的 5~10 条（跳过 system 和已折叠区域）划为一个 span。
    """
    protected_start = max(0, len(messages) - KEEP_RECENT)
    candidates = messages[:protected_start]

    committed_ids = _get_all_committed_ids(messages, store)
    
    current_span = []
    
    def try_stage_current_span():
        if len(current_span) >= 5:  # 至少积累 5 条才作为一个 span
            if current_span[0].id and current_span[-1].id:
                existing = any(s.start_uuid == current_span[0].id for s in store.staged)
                if not existing:
                    store.staged.append(StagedSpan(
                        start_uuid=current_span[0].id,
                        end_uuid=current_span[-1].id,
                        risk=0.5
                    ))
                    logger.debug(
                        "Staged span for collapse",
                        length=len(current_span),
                        start_id=current_span[0].id[:8],
                        end_id=current_span[-1].id[:8],
                        total_staged=len(store.staged),
                    )
        current_span.clear()

    for msg in candidates:
        if isinstance(msg, SystemMessage):
            try_stage_current_span()
            continue
            
        if msg.id and msg.id in committed_ids:
            try_stage_current_span()
            continue
            
        current_span.append(msg)

    try_stage_current_span()


async def _commit_staged(messages: List[BaseMessage], store: CollapseStore, limit: Optional[int]):
    """
    将 staging 队列中的 span，生成摘要并持久化为 Commit。
    """
    if not store.staged:
        return
        
    to_commit = store.staged[:limit] if limit else store.staged
    
    committed: list[StagedSpan] = []
    for span in to_commit:
        span_msgs = _extract_messages_by_range(messages, span.start_uuid, span.end_uuid)
        if not span_msgs:
            continue

        try:
            summary = await _generate_span_summary(span_msgs)
        except Exception:
            logger.exception("Context Collapse 摘要生成失败，保留原始消息")
            continue

        commit = CollapseCommit(
            collapse_id=uuid.uuid4().hex[:16],
            summary_uuid=str(uuid.uuid4()),
            summary_content=summary,
            first_msg_uuid=span.start_uuid,
            last_msg_uuid=span.end_uuid
        )

        store.add_commit(commit)
        committed.append(span)

        logger.info(
            "Context Collapse 提交完成",
            collapse_id=commit.collapse_id,
            msg_count=len(span_msgs)
        )

    store.staged = [span for span in store.staged if span not in committed]


def _project_view(messages: List[BaseMessage], store: CollapseStore) -> List[BaseMessage]:
    """ 根据当前的 commits 信息，将 messages 中被折叠的部分替换为 <collapsed> 标签。

    Args:
        messages: 当前上下文消息列表（原始视图）
        store: CollapseStore 实例，包含当前的 commits 和 staged 信息

    Returns:
        List[BaseMessage]: 投影后的消息列表，其中被 commits 包裹的消息被替换为 <collapsed> 标签的 SystemMessage。

    """
    if not store.commits:
        return messages

    commit_boundaries = {}
    for c in store.commits:
        commit_boundaries[c.first_msg_uuid] = c
        commit_boundaries[c.last_msg_uuid] = c
        
    committed_ids = _get_all_committed_ids(messages, store)
    
    projected = []
    
    for msg in messages:
        if msg.id and msg.id in committed_ids:
            # 这条消息被折叠了。
            # 如果它是这个折叠块的开头，我们在此处插入占位符
            c = commit_boundaries.get(msg.id)
            if c and c.first_msg_uuid == msg.id:
                summary_msg = SystemMessage(
                    content=f'<collapsed id="{c.collapse_id}">\n{c.summary_content}\n</collapsed>',
                    id=c.summary_uuid,
                    additional_kwargs=internal_context_metadata(
                        "collapse",
                        is_collapsed_summary=True,
                    ),
                )
                projected.append(summary_msg)
            continue

        projected.append(msg)
        
    return projected

async def _generate_span_summary(span_messages: List[BaseMessage]) -> str:
    """ 使用 LLM 生成 span 的摘要文本。摘要应该极简地总结 span_messages 中的核心信息，包括关键事实、结论、文件路径或实体状态等，确保不遗漏任何影响后续任务的内容。

    Args:
        span_messages: 需要生成摘要的消息列表，通常包含连续的一段对话和工具调用记录。这些消息已经被确定为一个待折叠的区段。

    Returns:
        str: 生成的摘要文本，应该极简地总结 span_messages 中的核心信息，包括关键事实、结论、文件路径或实体状态等，确保不遗漏任何影响后续任务的内容。

    """
    from server.agent.llm_factory import get_utility_llm
    llm = get_utility_llm()
    
    conversation = ""
    for m in span_messages:
        text = str(m.content)
        if len(text) > 1000:
            text = text[:1000] + "...(truncated)"
        conversation += f"[{m.type}]: {text}\n"
        
    prompt = global_prompt_runtime.render(
        "compression.collapse_summary", conversation=conversation
    )
    from core.model_runtime.usage import bind_usage_purpose

    with bind_usage_purpose("compact"):
        resp = await invoke_internal_model(llm, [HumanMessage(content=prompt)])
    return str(resp.content)


def _get_all_committed_ids(messages: List[BaseMessage], store: CollapseStore) -> Set[str]:
    """ 扫描当前 messages，结合 store 中的 commits 信息，找出所有被折叠掉的消息 ID。

    Args:
        messages: 当前上下文消息列表（原始视图）
        store: CollapseStore 实例，包含当前的 commits 和 staged 信息

    Returns:
        Set[str]: 当前 messages 中所有被 commits 包裹的消息 ID 集合。这些消息应该在投影视图中被替换为 <collapsed> 标签。

    """
    committed_ids = set()
    for c in store.commits:
        in_range = False
        for m in messages:
            if m.id == c.first_msg_uuid:
                in_range = True
            if in_range and m.id:
                committed_ids.add(m.id)
            if m.id == c.last_msg_uuid:
                break
    return committed_ids


def _extract_messages_by_range(messages: List[BaseMessage], start_uuid: str, end_uuid: str) -> List[BaseMessage]:
    """根据首尾 UUID 提取消息子列表"""
    span_msgs = []
    in_range = False
    for m in messages:
        if m.id == start_uuid:
            in_range = True
        if in_range:
            span_msgs.append(m)
        if m.id == end_uuid:
            break
    return span_msgs
