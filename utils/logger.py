import logging
import logging.config
import os
import structlog
import sys
from dataclasses import dataclass
from pathlib import Path


def _add_telemetry_context(_, __, event_dict):
    """Attach trace correlation IDs when the current task is instrumented."""
    try:
        from core.observability.context import current_telemetry_context

        context = current_telemetry_context()
        if context is not None:
            for key in ("request_id", "trace_id", "span_id", "session_id", "turn_id", "worker_id"):
                value = getattr(context, key, None)
                if value is not None:
                    event_dict.setdefault(key, value)
    except Exception:
        pass
    return event_dict


@dataclass(frozen=True)
class LoggingOptions:
    base_dir: Path
    level: int
    stdout: bool
    retention_days: int


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def logging_options() -> LoggingOptions:
    """Read logging settings without exposing credentials or application config."""
    base_dir = Path(os.getenv("NLP_AGENT_LOG_DIR", "logs").strip() or "logs").expanduser()
    service = os.getenv("NLP_AGENT_LOG_SERVICE", "").strip()
    if service:
        base_dir /= service

    level_name = os.getenv("NLP_AGENT_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    try:
        retention_days = max(1, int(os.getenv("NLP_AGENT_LOG_RETENTION_DAYS", "14")))
    except ValueError:
        retention_days = 14

    return LoggingOptions(
        base_dir=base_dir,
        level=level,
        stdout=_env_bool("NLP_AGENT_LOG_STDOUT", False),
        retention_days=retention_days,
    )

def setup_logging():
    '''
    初始化日志系统，配置日志格式和输出方式
    '''
    options = logging_options()

    # 1. 定义共用的处理器
    shared_processors = [
        _add_telemetry_context,
        structlog.stdlib.add_log_level,                 # 添加日志级别 (info, error)
        structlog.stdlib.add_logger_name,               # 添加 Logger 名称
        structlog.processors.TimeStamper(fmt="iso"),    # ISO 8601 格式时间戳
        structlog.processors.StackInfoRenderer(),       # 错误发生时的调用栈
        structlog.processors.format_exc_info,           # 格式化 Exception
        structlog.processors.UnicodeDecoder(),          # 统一字符编码
    ]

    # 2. 配置标准 logging 模块
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(ensure_ascii=False),
                ],
                "foreign_pre_chain": shared_processors,
            },
            "json_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(ensure_ascii=False),
                ],
                "foreign_pre_chain": shared_processors,
            },
        },
        "handlers": {
            "console": (
                {
                    "class": "logging.StreamHandler",
                    "level": options.level,
                    "formatter": "json_formatter",
                    "stream": "ext://sys.stdout",
                }
                if options.stdout
                else {"class": "logging.NullHandler"}
            ),
        },
        "loggers": {
            "": {
                "handlers": ["console"],
                "level": options.level,
            },
            # 第三方库静默 — 避免 httpx/openai/chromadb 的请求日志污染终端
            "httpx": {"level": "WARNING"},
            "openai": {"level": "WARNING"},
            "chromadb": {"level": "WARNING"},
            "bm25s": {"level": "WARNING"},
        }
    })

    # 按日期目录写入（dictConfig 不支持自定义类，手动挂到 root logger）
    from utils.daily_handler import DailyDirectoryHandler

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=shared_processors,
    )

    for h in (
        DailyDirectoryHandler(
            base_dir=options.base_dir,
            level=logging.NOTSET,
            retention_days=options.retention_days,
        ),
        DailyDirectoryHandler(
            base_dir=options.base_dir,
            level=logging.WARNING,
            retention_days=options.retention_days,
        ),
        DailyDirectoryHandler(
            base_dir=options.base_dir,
            level=logging.ERROR,
            retention_days=options.retention_days,
        ),
    ):
        h.setFormatter(json_formatter)
        logging.root.addHandler(h)

    # 3. 配置 Structlog 顶层包装
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# 在模块导入时自动执行配置
setup_logging()

# 导出一个便捷获取 logger 的函数
def get_logger(name: str = __name__):
    return structlog.get_logger(name)
