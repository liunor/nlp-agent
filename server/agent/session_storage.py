"""
通用 NLP 智能体 - 会话持久化模块 (JSONL Transcript)

该模块借鉴 Claude Code 的存储设计，将基于 LangGraph 的会话记录追加保存为单文件 JSONL。
主要特性：
1. 增量写入：使用队列和异步定时任务（drain_write_queue）批量刷入磁盘，避免每次都全量覆写。
2. UUID 树形指针：每条消息记录自己的 `uuid` 与前一条消息的 `parent_uuid`，从而支持完美的状态重构和分支。
3. 纯粹性：此持久化仅用作对话 Transcript（副本），LangGraph 的内部调度状态依然由 SQLite 管理。
"""

import os
import json
import time
import asyncio
import threading
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from utils.logger import get_logger
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage

from utils.tokens import token_count_with_estimation, rough_estimation_for_messages

logger = get_logger("shiliu.session_storage")

from core.session_context import SessionContext
from server.agent.compression.internal_context import public_transcript_messages


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_HISTORY_DIR = str(_PROJECT_ROOT / ".data" / "chat_history")
os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)

SESSIONS_INDEX_PATH = os.path.join(CHAT_HISTORY_DIR, ".sessions.json")
_INDEX_LOCK = threading.RLock()


def get_session_transcript_path(session_id: str) -> str:
    """获取主会话 transcript.jsonl 的绝对路径。"""
    SessionContext(session_id=session_id)
    return os.path.join(CHAT_HISTORY_DIR, f"{session_id}.jsonl")


def _load_sessions_index() -> dict:
    """读取会话索引文件。"""
    with _INDEX_LOCK:
        if not os.path.exists(SESSIONS_INDEX_PATH):
            return {"active_session": None, "sessions": {}}
        try:
            with open(SESSIONS_INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"active_session": None, "sessions": {}}


def _save_sessions_index(index: dict):
    """写入会话索引文件。"""
    with _INDEX_LOCK:
        temporary = f"{SESSIONS_INDEX_PATH}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, SESSIONS_INDEX_PATH)


def get_active_session_id() -> str:
    """获取上次活跃的会话 ID，若索引中无记录则返回 None。"""
    index = _load_sessions_index()
    active = index.get("active_session")
    if active and active in index.get("sessions", {}):
        return active
    return None


def create_new_session() -> str:
    """创建新会话，写入索引，返回 session_id。"""
    session_id = SessionContext.create().session_id
    index = _load_sessions_index()
    index["active_session"] = session_id
    index["sessions"][session_id] = {
        "created_at": time.time(),
        "last_active": time.time(),
    }
    _save_sessions_index(index)
    return session_id


def switch_to_new_session() -> str:
    """结束当前活跃会话，创建新会话。旧会话的 JSONL 保留在磁盘上。"""
    index = _load_sessions_index()
    now = time.time()
    old_id = index.get("active_session")
    if old_id and old_id in index.get("sessions", {}):
        index["sessions"][old_id]["last_active"] = now
    new_id = SessionContext.create().session_id
    index["active_session"] = new_id
    index["sessions"][new_id] = {
        "created_at": now,
        "last_active": now,
    }
    _save_sessions_index(index)
    return new_id


def save_session_on_exit():
    """退出前更新活跃会话的最后活跃时间。"""
    index = _load_sessions_index()
    active = index.get("active_session")
    if active and active in index.get("sessions", {}):
        index["sessions"][active]["last_active"] = time.time()
        _save_sessions_index(index)


def load_session(session_id: str) -> bool:
    """切换到指定会话。若会话在索引中存在则返回 True，否则返回 False。"""
    index = _load_sessions_index()
    if session_id not in index.get("sessions", {}):
        return False
    # 更新旧活跃会话的 last_active
    old_id = index.get("active_session")
    if old_id and old_id in index.get("sessions", {}):
        index["sessions"][old_id]["last_active"] = time.time()
    # 切换到目标会话
    index["active_session"] = session_id
    index["sessions"][session_id]["last_active"] = time.time()
    _save_sessions_index(index)
    return True


def list_sessions() -> list:
    """列出所有历史会话摘要。"""
    index = _load_sessions_index()
    result = []
    for sid, info in index.get("sessions", {}).items():
        transcript_path = get_session_transcript_path(sid)
        msg_count = 0
        if os.path.exists(transcript_path):
            try:
                with open(transcript_path, "r", encoding="utf-8") as f:
                    msg_count = sum(1 for _ in f)
            except Exception:
                pass
        result.append({
            "session_id": sid,
            "created_at": info.get("created_at", 0),
            "last_active": info.get("last_active", 0),
            "message_count": msg_count,
            "is_active": sid == index.get("active_session"),
        })
    result.sort(key=lambda x: x["last_active"], reverse=True)
    return result


class TranscriptMessage(BaseModel):
    uuid: str = Field(description="当前消息的全局唯一ID")
    parentUuid: Optional[str] = Field(default=None, description="指向上一条消息的ID，形成链表或树")
    sessionId: str = Field(description="所属的会话ID")
    timestamp: float = Field(default_factory=time.time, description="写入时间戳")
    type: str = Field(description="消息类型: user | assistant | tool_result | system")
    role: str = Field(description="对应 API 的 role: user | assistant | tool | system")
    content: Any = Field(description="消息内容")
    toolCalls: Optional[List[Dict[str, Any]]] = Field(default=None, description="工具调用列表，仅 type=assistant 时存在")
    toolResult: Optional[Dict[str, Any]] = Field(default=None, description="工具执行结果，仅 type=tool_result 时存在")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="API消耗的精确token(usage_metadata)")


class SessionStorageManager:
    """
    负责管理内存写队列，并在后台定时批量将 JSONL 数据刷入磁盘。
    """
    def __init__(self):
        # session_id -> List[TranscriptMessage]
        self._write_queues: Dict[str, List[TranscriptMessage]] = {}
        # 记录已写入内存队列 of UUID（含已落盘），防重去重
        self._written_uuids: Set[tuple[str, str]] = set()
        self._loaded_sessions: Set[str] = set()
        self._drain_task: Optional[asyncio.Task] = None
        self._is_draining = False
        self._file_lock = threading.Lock()

    def ensure_drain_task(self):
        """确保后台刷盘任务正在运行。"""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def _drain_loop(self):
        """每隔 100ms 检查队列并刷入磁盘。"""
        try:
            while True:
                await asyncio.sleep(0.1)
                await self.flush()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Session Storage 刷盘循环发生异常: {e}")

    async def flush(self):
        """强行排空写队列并刷盘。"""
        if self._is_draining:
            return
        self._is_draining = True
        try:
            # 取出现有队列，防止异步操作期间有新数据插入
            current_queues = self._write_queues
            self._write_queues = {}
            
            for session_id, queue in current_queues.items():
                if not queue:
                    continue
                file_path = get_session_transcript_path(session_id)
                content = ""
                for msg in queue:
                    content += msg.model_dump_json(exclude_none=True) + "\n"

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._append_to_file, file_path, content)
                logger.debug(f"已向 {file_path} 批量刷入 {len(queue)} 条日志")
        finally:
            self._is_draining = False

    async def close(self) -> None:
        """Flush pending transcript rows and stop the background writer."""
        await self.flush()
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        self._drain_task = None

    def _append_to_file(self, file_path: str, content: str):
        with self._file_lock:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(content)

    def to_transcript(self, msg: BaseMessage, session_id: str, parent_uuid: Optional[str]) -> TranscriptMessage:
        msg_type = "user"
        msg_role = "user"
        tool_calls = None
        tool_result = None
        content = msg.content
        usage = getattr(msg, "usage_metadata", None)

        if isinstance(msg, HumanMessage):
            msg_type = "user"
            msg_role = "user"
        elif isinstance(msg, AIMessage):
            msg_type = "assistant"
            msg_role = "assistant"
            if msg.tool_calls:
                tool_calls = [
                    {
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": tc.get("args")
                    }
                    for tc in msg.tool_calls
                ]
        elif isinstance(msg, ToolMessage):
            msg_type = "tool_result"
            msg_role = "tool"
            tool_result = {
                "tool_call_id": msg.tool_call_id,
                "content": msg.content
            }
        elif isinstance(msg, SystemMessage):
            msg_type = "system"
            msg_role = "system"
        else:
            t = getattr(msg, "type", "human")
            if t == "human":
                msg_type = "user"
                msg_role = "user"
            elif t == "ai":
                msg_type = "assistant"
                msg_role = "assistant"
            elif t == "tool":
                msg_type = "tool_result"
                msg_role = "tool"
                tool_result = {
                    "tool_call_id": getattr(msg, "tool_call_id", ""),
                    "content": msg.content
                }
            elif t == "system":
                msg_type = "system"
                msg_role = "system"

        return TranscriptMessage(
            uuid=msg.id or "",
            parentUuid=parent_uuid,
            sessionId=session_id,
            timestamp=time.time(),
            type=msg_type,
            role=msg_role,
            content=content,
            toolCalls=tool_calls,
            toolResult=tool_result,
            usage=usage
        )

    async def append_entry(self, entry: dict, *, session_id: str):
        """写入任意 JSONL 记录（例如 context collapse commit）。"""
        SessionContext(session_id=session_id)
        file_path = get_session_transcript_path(session_id)
        # 添加换行符
        content = json.dumps(entry, ensure_ascii=False) + "\n"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_to_file, file_path, content)

    def enqueue_messages(self, session_id: str, messages: List[BaseMessage]):
        """
        比对当前传入的完整消息链，只将新产生的增量消息压入写队列。
        """
        if not messages:
            return

        self._load_written_ids(session_id)

        parent_uuid = None
        new_entries = []

        for msg in public_transcript_messages(messages):
            msg_uuid = msg.id
            if msg_uuid is None:
                continue
            
            message_key = (session_id, msg_uuid)
            if message_key not in self._written_uuids:
                entry = self.to_transcript(msg, session_id, parent_uuid)
                new_entries.append(entry)
                self._written_uuids.add(message_key)
            
            # 更新 parent_uuid，准备用于下一条
            parent_uuid = msg_uuid
        
        if new_entries:
            if session_id not in self._write_queues:
                self._write_queues[session_id] = []
            self._write_queues[session_id].extend(new_entries)
            self.ensure_drain_task()

    def _load_written_ids(self, session_id: str) -> None:
        """Load persisted IDs once so process restarts do not duplicate JSONL rows."""
        if session_id in self._loaded_sessions:
            return
        self._loaded_sessions.add(session_id)
        path = get_session_transcript_path(session_id)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    message_id = payload.get("uuid")
                    if message_id:
                        self._written_uuids.add((session_id, str(message_id)))
        except Exception as error:
            logger.warning("加载已写入消息 ID 失败", session_id=session_id, error=str(error))

global_session_storage = SessionStorageManager()

async def record_transcript(
    session_id: str,
    messages: List[BaseMessage],
    *,
    user_id: str | None = None,
    workspace_id: str | None = None,
):
    """
    暴露给上层的封装调用：对比当前 Graph 内的 messages 链，提取并附加新消息。
    """
    """Persist the current transcript snapshot in MySQL; JSONL is legacy-read only."""
    from sqlalchemy import create_engine, delete, text
    from sqlalchemy.dialects.mysql import insert
    from configs.settings import settings
    from server.infrastructure.mysql.models import ConversationTranscriptModel

    def persist() -> None:
        url = settings.NLP_AGENT_DATABASE_URL.strip()
        if not url:
            raise RuntimeError("NLP_AGENT_DATABASE_URL is required for transcript storage")
        engine = create_engine(url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                if not user_id or not workspace_id:
                    raise PermissionError("transcript identity is required")
                owner = connection.execute(
                    text(
                        "SELECT 1 FROM nlp_conversations "
                        "WHERE id=:session_id AND owner_user_id=:user_id "
                        "AND workspace_id=:workspace_id AND status='active'"
                    ),
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                    },
                ).first()
                if owner is None:
                    raise PermissionError("transcript does not belong to the principal")
                connection.execute(delete(ConversationTranscriptModel).where(ConversationTranscriptModel.session_id == session_id))
                parent_id = None
                for index, message in enumerate(public_transcript_messages(messages)):
                    message_id = str(getattr(message, "id", None) or uuid.uuid4())
                    role = "assistant" if isinstance(message, AIMessage) else "tool" if isinstance(message, ToolMessage) else "system" if isinstance(message, SystemMessage) else "user"
                    connection.execute(insert(ConversationTranscriptModel).values(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{message_id}:{index}")), session_id=session_id, message_uuid=message_id, parent_uuid=parent_id, message_type=message.__class__.__name__, role=role, content_json={"content": message.content}, tool_json={"tool_calls": getattr(message, "tool_calls", [])} if getattr(message, "tool_calls", None) else None, usage_json=getattr(message, "usage_metadata", None)))
                    parent_id = message_id
        finally:
            engine.dispose()

    await asyncio.to_thread(persist)

async def load_transcript_file(session_id: str) -> List[BaseMessage]:
    """
    从 JSONL 恢复会话记录：读取所有条目，并通过 parentUuid 重建因果链表（解决分支分叉问题）。
    返回正确的有序 BaseMessage 列表。
    """
    file_path = get_session_transcript_path(session_id)
    if not os.path.exists(file_path):
        return []

    entries_map: Dict[str, TranscriptMessage] = {}
    leaf_uuid: Optional[str] = None
    latest_ts = 0.0
    collapse_commits = []

    # 解析 JSONL
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry_dict = json.loads(line)
                    
                    if entry_dict.get("type") == "marble-origami-commit":
                        collapse_commits.append(entry_dict)
                        continue
                        
                    entry = TranscriptMessage(**entry_dict)
                    entries_map[entry.uuid] = entry

                    # 假定最后一条写入的消息为默认的叶子节点，如果有多个分支，则取时间戳最新的
                    # 使用 >= 处理在同一毫秒内批量写入的多个消息记录
                    if entry.timestamp >= latest_ts:
                        latest_ts = entry.timestamp
                        leaf_uuid = entry.uuid
                except Exception as ex:
                    logger.warning(f"解析 JSONL 行失败: {ex}")
    except Exception as e:
        logger.error(f"读取 {file_path} 失败: {e}")
        return []

    # Legacy transcript commits are migrated into the per-session context state.
    if collapse_commits:
        try:
            from core.session_context import local_context_repository

            context = SessionContext(session_id=session_id)
            state = local_context_repository.load(context)
            if not state.collapse_commits:
                local_context_repository.save(
                    context,
                    state.model_copy(update={"collapse_commits": collapse_commits}),
                )
        except Exception as e:
            logger.warning(f"迁移 Collapse Commit Log 失败: {e}")

    if not leaf_uuid:
        return []

    # 从最新的叶子节点沿 parentUuid 往上溯源重建单链
    ordered_uuids = []
    current_uuid = leaf_uuid
    
    while current_uuid:
        ordered_uuids.append(current_uuid)
        entry = entries_map.get(current_uuid)
        if entry:
            current_uuid = entry.parentUuid
        else:
            current_uuid = None

    ordered_uuids.reverse()

    from server.agent.compression.session_compression import compress_messages

    # 应用上下文窗口压缩逻辑
    ordered_entries = [entries_map[uid] for uid in ordered_uuids if uid in entries_map]
    compressed_entries = compress_messages(ordered_entries)

    # 反序列化为 LangChain BaseMessage 实例
    reconstructed_messages = []
    for entry in compressed_entries:
        if entry.type == "user":
            msg = HumanMessage(content=entry.content, id=entry.uuid)
        elif entry.type == "assistant":
            tool_calls = []
            if entry.toolCalls:
                tool_calls = [
                    {
                        "name": tc.get("name"),
                        "args": tc.get("input", {}),
                        "id": tc.get("id"),
                        "type": "tool_call"
                    }
                    for tc in entry.toolCalls
                ]
            msg = AIMessage(content=entry.content, tool_calls=tool_calls, id=entry.uuid)
            if entry.usage:
                msg.usage_metadata = entry.usage
        elif entry.type == "tool_result":
            tool_call_id = ""
            if entry.toolResult:
                tool_call_id = entry.toolResult.get("tool_call_id", "")
            msg = ToolMessage(content=entry.content, tool_call_id=tool_call_id, id=entry.uuid)
        elif entry.type == "system":
            msg = SystemMessage(content=entry.content, id=entry.uuid)
        else:
            msg = HumanMessage(content=entry.content, id=entry.uuid)
        reconstructed_messages.append(msg)

    # Legacy JSONL files may predate the internal metadata marker. Apply the
    # content-based compatibility filter after reconstruction as well.
    return public_transcript_messages(reconstructed_messages)
