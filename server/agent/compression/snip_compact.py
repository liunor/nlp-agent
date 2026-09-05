"""
Snip 压缩核心模块。

这是上下文窗口压缩的第三层：直接移除对话开头一批老消息，并插入边界标记。
不做任何摘要，不调用 LLM，零 API 开销。

核心逻辑：
1. 检查当前消息列表的 Token 用量是否超过 SNIP_THRESHOLD。
2. 超过后，从头部开始扫描，砍掉最老的 user+assistant 轮次（保护最近 KEEP_RECENT 条）。
3. 被砍的消息替换为 snip_marker 占位消息（仍在列表里，但 content 极小，不占上下文）。
4. 在被砍区域末尾插入 snip_boundary 边界消息，通知模型上下文已截断。
5. 返回 SnipCompactResult(messages, tokens_freed, boundary_message)。

辅助能力：
- derive_short_id(): 从消息 ID 生成 6位 base36 短 ID，供 SnipTool 按 ID 范围 snip。
- is_snip_marker_message(): 判断一条消息是否为 snip 占位桩。
- is_snip_boundary_message(): 判断一条消息是否为 snip 边界标记。
- should_nudge_for_snips(): 每 ~10K token 没有 snip 时返回 True，提示 coordinator 主动 snip。
"""

import time
from typing import List, Optional, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from server.agent.compression.internal_context import internal_context_metadata
from utils.tokens import token_count_with_estimation, rough_estimation_for_messages
from utils.logger import get_logger

logger = get_logger("shiliu.snip_compact")


# Snip 触发阈值：超过该值才考虑执行 snip（单位：token）
# 设为 MAX_CONTEXT_TOKENS 的 75%，给 LLM 留出足够的响应空间
SNIP_THRESHOLD = 150_000

# 最近 N 条消息受保护，不参与 snip（确保当前对话上下文完整）
KEEP_RECENT = 10

# 每次 snip 释放的目标 token 量（释放够了就停）
SNIP_TARGET_FREE = 30_000

# 距上次 snip 多少 token 后提示 coordinator 主动 snip
NUDGE_INTERVAL_TOKENS = 10_000

# snip 占位消息和边界消息的 metadata 标记键
_SNIP_MARKER_KEY = "snip_marker"
_SNIP_BOUNDARY_KEY = "snip_boundary"

def derive_short_id(msg_id: str) -> str:
    """
    从消息 UUID 推导 6 位 base36 短 ID。

    对应 Claude Code 的 deriveShortMessageId()：
      const hex = uuid.replace(/-/g, '').slice(0, 10)
      return parseInt(hex, 16).toString(36).slice(0, 6)

    供 SnipTool 通过 [id:XXXXXX] 标签引用消息。
    """
    hex_str = msg_id.replace("-", "")[:10]
    try:
        num = int(hex_str, 16)
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        result = ""
        if num == 0:
            return "0" * 6
        while num:
            result = chars[num % 36] + result
            num //= 36
        return result[:6].rjust(6, "0")
    except ValueError:
        return msg_id[:6]


def get_short_id(msg: BaseMessage) -> str:
    """获取消息的短 ID，若无 id 则返回空串。"""
    if msg.id:
        return derive_short_id(msg.id)
    return ""

def is_snip_marker_message(msg: BaseMessage) -> bool:
    """判断是否为 snip 占位桩（已被砍掉消息的替身）。"""
    meta = getattr(msg, "additional_kwargs", {}) or {}
    return meta.get(_SNIP_MARKER_KEY, False) is True


def is_snip_boundary_message(msg: BaseMessage) -> bool:
    """判断是否为 snip 边界标记（告诉模型这里发生了截断）。"""
    meta = getattr(msg, "additional_kwargs", {}) or {}
    return meta.get(_SNIP_BOUNDARY_KEY, False) is True


def _make_snip_marker(original_msg: BaseMessage) -> SystemMessage:
    """
    为被砍掉的消息创建占位桩。
    桩消息极小（几乎不占 token），但保留被砍消息的 id 以便后续 id 追踪。
    """
    return SystemMessage(
        content="[snipped]",
        id=original_msg.id,
        additional_kwargs={
            _SNIP_MARKER_KEY: True,
            **internal_context_metadata("snip_marker"),
        },
    )


def _make_snip_boundary(
    tokens_freed: int,
    messages_removed: int,
    snipped_at: float,
) -> SystemMessage:
    """
    创建 snip 边界标记，告诉模型「这里之前的内容已被压缩」。
    边界消息出现在被砍区域的末尾，模型可以从这里开始正常读取上下文。
    """
    return SystemMessage(
        content=(
            f"[Context Snip Boundary]\n"
            f"为节省上下文空间，此处之前的 {messages_removed} 条历史消息已被移除。\n"
            f"释放了约 {tokens_freed:,} tokens。\n"
            f"当前对话从此处继续，你可以照常回复，无需提及这次压缩。"
        ),
        additional_kwargs={
            _SNIP_BOUNDARY_KEY: True,
            **internal_context_metadata("snip_boundary"),
            "tokens_freed": tokens_freed,
            "messages_removed": messages_removed,
            "snipped_at": snipped_at,
        }
    )


class SnipCompactResult:
    """snip_compact_if_needed 的返回值。"""
    def __init__(self, messages: List[BaseMessage], tokens_freed: int = 0, boundary_message=None):
        self.messages = messages
        self.tokens_freed = tokens_freed
        self.boundary_message = boundary_message  # 若发生 snip，则为 SystemMessage；否则为 None


def snip_compact_if_needed(
    messages: List[BaseMessage],
    force: bool = False,
    *,
    threshold: int = SNIP_THRESHOLD,
    target_free: int = SNIP_TARGET_FREE,
    keep_recent: int = KEEP_RECENT,
) -> SnipCompactResult:
    """
    检查 token 用量，必要时从头部移除旧消息并插入边界标记。

    Args:
        messages: 当前完整消息列表（含 SystemMessage）。
        force: 强制执行 snip，无论当前 token 是否超阈值（用于 snipReplay）。

    Returns:
        SnipCompactResult(messages, tokens_freed, boundary_message)
        - messages: 处理后的新消息列表
        - tokens_freed: 本次释放的估算 token 数
        - boundary_message: 若发生了 snip，为插入的边界 SystemMessage；否则 None
    """
    if not messages:
        return SnipCompactResult(messages)

    current_tokens = token_count_with_estimation(messages)

    if not force and current_tokens <= threshold:
        return SnipCompactResult(messages)

    result_msgs, tokens_freed, boundary_msg = _do_snip(
        messages,
        current_tokens,
        target_free=target_free,
        keep_recent=keep_recent,
    )

    if tokens_freed > 0:
        logger.info(
            "Snip 压缩完成",
            extra={
                "original_count": len(messages),
                "result_count": len(result_msgs),
                "tokens_freed": tokens_freed,
                "tokens_before": current_tokens,
                "tokens_after": current_tokens - tokens_freed,
            }
        )

    return SnipCompactResult(result_msgs, tokens_freed, boundary_msg)


def snip_by_id_range(
    messages: List[BaseMessage],
    to_id: str,
    from_id: Optional[str] = None,
) -> SnipCompactResult:
    """
    按消息短 ID 范围执行 snip（供 SnipTool 调用）。

    移除所有 short_id 在 [from_id, to_id] 之间的消息（含边界），
    替换为 snip_marker 桩，并在末尾插入 snip_boundary。

    Args:
        messages: 当前消息列表
        to_id: snip 区域的结束短 ID（含，该消息也会被移除）
        from_id: snip 区域的起始短 ID（含）。为 None 时从最早的非 system 消息开始。

    Returns:
        SnipCompactResult
    """
    if not messages:
        return SnipCompactResult(messages)

    # 建立 short_id → 消息的映射
    id_map = {get_short_id(m): i for i, m in enumerate(messages) if m.id}

    # 找到 to 位置
    to_idx = id_map.get(to_id)
    if to_idx is None:
        logger.warning("SnipTool: 未找到 to_id 对应消息", to_id=to_id)
        return SnipCompactResult(messages)

    # 找到 from 位置（默认从第一条非 system 消息）
    if from_id:
        from_idx = id_map.get(from_id)
        if from_idx is None:
            logger.warning("SnipTool: 未找到 from_id 对应消息", from_id=from_id)
            return SnipCompactResult(messages)
    else:
        from_idx = None
        for i, m in enumerate(messages):
            if not isinstance(m, SystemMessage) and not is_snip_marker_message(m):
                from_idx = i
                break
        if from_idx is None:
            return SnipCompactResult(messages)

    if from_idx > to_idx:
        logger.warning("SnipTool: from_id 在 to_id 之后，跳过", from_idx=from_idx, to_idx=to_idx)
        return SnipCompactResult(messages)

    protected_start = max(0, len(messages) - KEEP_RECENT)
    if from_idx >= protected_start:
        logger.warning("SnipTool: 目标范围在受保护区域内，跳过")
        return SnipCompactResult(messages)

    actual_to_idx = min(to_idx, protected_start - 1)

    to_snip = messages[from_idx: actual_to_idx + 1]
    tokens_freed = rough_estimation_for_messages(to_snip)
    messages_removed = len(to_snip)

    now = time.time()
    boundary_msg = _make_snip_boundary(tokens_freed, messages_removed, now)

    new_msgs = []
    for i, m in enumerate(messages):
        if i < from_idx or i > actual_to_idx:
            new_msgs.append(m)
        elif i == from_idx:
            new_msgs.append(boundary_msg)
            new_msgs.append(_make_snip_marker(m))
        else:
            new_msgs.append(_make_snip_marker(m))

    logger.info(
        "SnipTool 按 ID 范围压缩完成",
        extra={
            "from_idx": from_idx,
            "to_idx": actual_to_idx,
            "tokens_freed": tokens_freed,
            "messages_removed": messages_removed,
        }
    )
    return SnipCompactResult(new_msgs, tokens_freed, boundary_msg)


def should_nudge_for_snips(messages: List[BaseMessage]) -> bool:
    """
    判断是否需要在系统提示中插入「上下文效率」提示，引导 Coordinator 主动调用 SnipTool。

    条件：距离上一次 snip（boundary 消息）之后又积累了 NUDGE_INTERVAL_TOKENS 的新内容。
    若从未 snip 过，则在总量超过 SNIP_THRESHOLD * 0.5 时开始提示。
    """
    last_boundary_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if is_snip_boundary_message(messages[i]):
            last_boundary_idx = i
            break

    if last_boundary_idx == -1:
        return token_count_with_estimation(messages) > SNIP_THRESHOLD * 0.5

    msgs_since_snip = messages[last_boundary_idx + 1:]
    return rough_estimation_for_messages(msgs_since_snip) > NUDGE_INTERVAL_TOKENS


def _do_snip(
    messages: List[BaseMessage],
    current_tokens: int,
    *,
    target_free: int = SNIP_TARGET_FREE,
    keep_recent: int = KEEP_RECENT,
) -> Tuple[List[BaseMessage], int, Optional[SystemMessage]]:
    """
    实际执行 snip：从头部移除旧的 user+assistant 轮次，直到释放了足够的 token。

    返回 (新消息列表, 释放的 token 数, boundary_message)
    """
    protected_start = max(0, len(messages) - keep_recent)
    candidates = messages[:protected_start]
    protected = messages[protected_start:]

    # 跳过最前面的 SystemMessage
    system_prefix: List[BaseMessage] = []
    non_sys_start = 0
    for i, m in enumerate(candidates):
        if isinstance(m, SystemMessage) or is_snip_boundary_message(m):
            system_prefix.append(m)
            non_sys_start = i + 1
        else:
            break

    snippable = candidates[non_sys_start:]  # 真正可以被砍的消息

    if not snippable:
        return messages, 0, None

    snipped: List[BaseMessage] = []
    tokens_freed = 0

    i = 0
    while i < len(snippable) and tokens_freed < target_free:
        msg = snippable[i]
        # 跳过已经是 marker 的消息
        if is_snip_marker_message(msg):
            i += 1
            continue
        # 尝试配对：human → ai
        if isinstance(msg, HumanMessage):
            pair = [msg]
            if i + 1 < len(snippable) and isinstance(snippable[i + 1], AIMessage):
                pair.append(snippable[i + 1])
                i += 2
            else:
                i += 1
        elif isinstance(msg, AIMessage):
            pair = [msg]
            i += 1
        else:
            # tool_result 等单独砍
            pair = [msg]
            i += 1

        pair_tokens = rough_estimation_for_messages(pair)
        snipped.extend(pair)
        tokens_freed += pair_tokens

    if not snipped:
        return messages, 0, None

    now = time.time()
    messages_removed = len(snipped)
    boundary_msg = _make_snip_boundary(tokens_freed, messages_removed, now)

    # 构建新消息列表：
    # [system_prefix] + [boundary_msg] + [markers for snipped] + [remaining snippable] + [protected]
    remaining_snippable = snippable[i:]
    markers = [_make_snip_marker(m) for m in snipped]

    new_msgs = system_prefix + [boundary_msg] + markers + remaining_snippable + protected

    return new_msgs, tokens_freed, boundary_msg
