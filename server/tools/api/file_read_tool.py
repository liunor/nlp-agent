import os
from pathlib import Path
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from core.session_context import SessionContext
from pydantic import BaseModel, Field


# 只允许读取项目 .data/ 目录下的文件，防止路径穿越攻击
# 基于项目根目录的绝对路径，不依赖 CWD
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ALLOWED_BASE = os.path.join(_PROJECT_ROOT, ".data")
# 单次读取行数上限
DEFAULT_READ_LIMIT = 500
MAX_READ_LIMIT = 2000


class ReadLocalFileInput(BaseModel):
    file_path: str = Field(description="要读取的文件的绝对路径（通常在 .data/chat_history/ 下的 tool-results 目录中）。")
    offset: int = Field(default=0, description="从第几行开始读取（0 表示第一行）。")
    limit: int = Field(default=DEFAULT_READ_LIMIT, description=f"最多读取多少行（默认 {DEFAULT_READ_LIMIT}，最大 {MAX_READ_LIMIT}）。")


@tool("read_local_file", args_schema=ReadLocalFileInput)
def read_local_file(file_path: str, config: RunnableConfig, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str:
    """按行读取本地文件。用于查看已持久化到磁盘的大型工具执行结果。
    只能读取 .data/ 目录下的文件。首次调用从 offset=0 开始，
    若输出被截断可增大 offset 继续读取后续内容。
    """
    try:
        context = SessionContext.from_config(config, require=True)
    except ValueError:
        return "安全限制：读取工具结果需要有效会话，请使用文件空间工具读取个人文件。"
    # Persisted tool results are scoped by the trusted conversation ID.
    allowed_results = Path(ALLOWED_BASE) / "chat_history" / context.session_id / "tool-results"
    # 安全检查：禁止路径穿越
    try:
        resolved = Path(file_path).resolve()
        allowed_base = allowed_results.resolve()
    except (OSError, RuntimeError):
        return f"错误：无法解析文件路径 '{file_path}'。"

    try:
        resolved.relative_to(allowed_base)
    except ValueError:
        return (
            f"安全限制：只允许读取 .data/ 目录下的文件。\n"
            f"请求路径：{file_path}\n"
            f"允许前缀：{ALLOWED_BASE}"
        )

    if not resolved.exists():
        return f"错误：文件不存在 '{resolved}'。"

    if not resolved.is_file():
        return f"错误：路径不是文件 '{resolved}'。"

    limit = max(1, min(limit, MAX_READ_LIMIT))
    offset = max(0, offset)

    try:
        with resolved.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"错误：文件不是 UTF-8 文本格式 '{resolved}'。"
    except Exception as e:
        return f"错误：读取文件失败 '{resolved}'：{e}"

    total_lines = len(lines)

    if offset >= total_lines:
        return f"[文件路径: {resolved}]\n[总行数: {total_lines}]\n\n错误：offset={offset} 超出文件总行数。"

    end = min(offset + limit, total_lines)
    selected = lines[offset:end]

    # 组装带行号的输出
    output_lines = []
    for i, line in enumerate(selected, start=offset + 1):
        output_lines.append(f"{i:>6}| {line.rstrip()}")

    header = (
        f"[文件路径: {resolved}]\n"
        f"[总行数: {total_lines} | 当前: 第 {offset + 1}-{end} 行]\n\n"
    )

    if end < total_lines:
        footer = f"\n\n[截断] 还有 {total_lines - end} 行未显示。使用 offset={end} 继续读取。"
    else:
        footer = ""

    return header + "\n".join(output_lines) + footer
