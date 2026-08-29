"""
Layer 5: Auto-Compact (全量摘要兜底)

在 Context Collapse 没压住（或者主动停用）时，作为兜底的全量压缩策略。
触发点：167K tokens (针对 200K 模型)。
压缩后会执行 Post-Compact Context Restoration，恢复核心上下文（如最新的文件操作状态）。
"""
import uuid
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from utils.tokens import rough_estimation_for_messages
from utils.logger import get_logger
from core.prompt_runtime import global_prompt_runtime
from server.agent.compression.message_integrity import remove_orphaned_tool_messages

logger = get_logger("shiliu.auto_compact")

AUTOCOMPACT_THRESHOLD = 167_000
MAX_CONSECUTIVE_FAILURES = 3

@dataclass
class AutoCompactResult:
    was_compacted: bool
    messages: List[BaseMessage]
    error: str = ""
    summary: str = ""

_consecutive_failures: dict[str, int] = {}


async def autocompact_if_needed(
    messages: List[BaseMessage],
    *,
    threshold: int = AUTOCOMPACT_THRESHOLD,
    session_id: str = "default",
) -> AutoCompactResult:
    """ 自动压缩函数，检查当前消息列表的 token 数量，如果超过阈值则执行全量压缩。

    Args:
        messages: 当前对话历史消息列表，包含 SystemMessage、HumanMessage、AIMessage 等类型。

    Returns:
        AutoCompactResult 对象，包含是否进行了压缩、最终的消息列表，以及可能的错误信息。
    """
    token_count = rough_estimation_for_messages(messages)
    if token_count < threshold:
        return AutoCompactResult(was_compacted=False, messages=messages)

    failures = _consecutive_failures.get(session_id, 0)
    if failures >= MAX_CONSECUTIVE_FAILURES:
        logger.warning(f"Auto-Compact 处于熔断状态（连续失败 {failures} 次），跳过压缩。")
        return AutoCompactResult(was_compacted=False, messages=messages, error="Circuit broken")
        
    logger.info(f"触发 Layer 5: Auto-Compact 全量压缩 (当前 Tokens: {token_count} >= {threshold})")
    
    try:
        KEEP_RECENT = 10
        if len(messages) <= KEEP_RECENT + 2:
            return AutoCompactResult(was_compacted=False, messages=messages)

        system_msgs = [m for m in messages if isinstance(m, SystemMessage) and not m.additional_kwargs.get("is_collapsed_summary")]
        other_msgs = [m for m in messages if m not in system_msgs]
        
        if len(other_msgs) <= KEEP_RECENT:
            return AutoCompactResult(was_compacted=False, messages=messages)
            
        to_compact = other_msgs[:-KEEP_RECENT]
        recent_kept = other_msgs[-KEEP_RECENT:]
        
        # 生成全局摘要
        summary_text = await _generate_global_summary(to_compact)
        
        # 构造压缩后的历史 (带摘要)
        summary_msg = SystemMessage(
            content=f"【历史对话摘要】\n以下是之前的详细对话和工具调用已被压缩：\n{summary_text}",
            id=str(uuid.uuid4())
        )
        
        # Post-Compact Context Restoration (重建核心状态)
        restored_context_msg = _build_post_compact_restoration(to_compact)
        
        new_messages = system_msgs + [summary_msg]
        if restored_context_msg:
            new_messages.append(restored_context_msg)
            
        new_messages.extend(recent_kept)
        new_messages = remove_orphaned_tool_messages(new_messages)
        
        _consecutive_failures[session_id] = 0
        logger.info("Auto-Compact 成功，上下文已大幅压缩并恢复核心状态。")

        return AutoCompactResult(
            was_compacted=True,
            messages=new_messages,
            summary=summary_text,
        )
        
    except Exception as e:
        _consecutive_failures[session_id] = failures + 1
        logger.exception("Auto-Compact 失败")
        return AutoCompactResult(was_compacted=False, messages=messages, error=str(e))


async def _generate_global_summary(messages: List[BaseMessage]) -> str:
    """ 生成全局摘要的函数，使用 LLM 来总结用户与 AI 的完整交互历史，重点保留未满足的需求、关键报错信息和重要文件路径。

    Args:
        messages: 需要被压缩的消息列表，包含用户与 AI 的完整交互历史（可能已经过局部折叠）。这些消息将被用来生成全局摘要。

    Returns:
        str: 生成的全局摘要文本，应该系统性总结用户未满足的需求、关键报错信息、重要文件路径等核心内容。
    """
    from server.agent.llm_factory import get_utility_llm
    llm = get_utility_llm()

    conversation = ""
    for m in messages:
        text = str(m.content)
        if len(text) > 2000:
            text = text[:1000] + "\n...(truncated)...\n" + text[-1000:]
        conversation += f"[{m.type}]: {text}\n"
        
    prompt = global_prompt_runtime.render("compression.auto_summary", conversation=conversation)
    from core.model_runtime.usage import bind_usage_purpose

    with bind_usage_purpose("compact"):
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
    return resp.content


def _build_post_compact_restoration(compacted_msgs: List[BaseMessage]) -> BaseMessage:
    """ 全量压缩后的上下文恢复函数，分析刚刚被压缩的消息列表，提取最近操作过的关键文件路径等核心状态信息，并构建一个系统消息来恢复这些核心上下文，以便用户在压缩后能够继续无缝操作。

    Args:
        compacted_msgs: 刚刚被全量压缩的消息列表。通过分析这些消息，提取最近操作过的关键文件路径等核心状态信息，以便在压缩后恢复上下文。

    Returns:
        SystemMessage: 包含恢复的核心上下文信息的系统消息，或者 None 如果没有需要恢复的核心状态。
    """
    recent_files = set()
    for m in compacted_msgs:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {})
                if name in ["read_local_file", "edit_local_file"]:
                    if "file_path" in args:
                        recent_files.add(args["file_path"])
                        
    if not recent_files:
        return None
        
    content = "【自动恢复的上下文】\n由于历史记录被压缩，系统为您恢复了最近操作过的关键文件路径，以便您随时重新读取：\n"
    for f in list(recent_files)[-5:]:
        content += f"- {f}\n"
        
    return SystemMessage(content=content, id=str(uuid.uuid4()))
