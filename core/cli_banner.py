"""Terminal startup banner: Nova ASCII logo + initial runtime snapshot.

Zero-dependency ANSI rendering. Colors are emitted only when stdout is a TTY
and ``NO_COLOR`` is unset; otherwise plain text so piped/Docker output stays
clean and database credentials are masked before they ever reach the terminal.
"""

from __future__ import annotations

import os
import sys
import tomllib
import unicodedata
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TAGLINE = "自然语言处理学习与实践助手"

# Block-style "NOVA" in the ANSI Shadow figlet font. Kept free of trailing
# whitespace so ``git diff --check`` stays clean.
NOVA_LOGO = (
    "███╗   ██╗ ██████╗ ██╗   ██╗ █████╗",
    "████╗  ██║██╔═══██╗██║   ██║██╔══██╗",
    "██╔██╗ ██║██║   ██║██║   ██║███████║",
    "██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║",
    "██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║",
    "╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝",
)

_CODES = {
    "cyan": "\x1b[36m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "yellow": "\x1b[33m",
    "green": "\x1b[32m",
}
_RESET = "\x1b[0m"


def _read_version() -> str:
    try:
        data = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version", "1.0.0"))
    except Exception:
        return "1.0.0"


def _enable_windows_ansi() -> bool:
    """Enable VT escape processing on Windows; no-op (True) elsewhere."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    return _enable_windows_ansi()


def _paint(text: str, code: str | None, color: bool) -> str:
    if not color or code is None:
        return text
    return f"{_CODES[code]}{text}{_RESET}"


def mask_dsn(url: str) -> str:
    """Mask credentials in a database DSN, keeping driver/host/port/db."""
    if not url:
        return ""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    authority, _, remainder = rest.partition("/")
    path, _, query = remainder.partition("?")
    masked_userinfo = ""
    if "@" in authority:
        userinfo, _, hostport = authority.rpartition("@")
        if ":" in userinfo:
            user, _, _ = userinfo.partition(":")
            masked_userinfo = f"{user}:***"
        else:
            masked_userinfo = userinfo
    else:
        hostport = authority
    masked = f"{scheme}://"
    if masked_userinfo:
        masked += f"{masked_userinfo}@"
    masked += hostport
    if path:
        masked += f"/{path}"
    if query:
        masked += f"?{query}"
    return masked


def _display_width(text: str) -> int:
    """Terminal column width, counting CJK/fullwidth glyphs as two cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text)


def _llm_line(conf: dict, default: str = "?") -> str:
    provider = conf.get("provider")
    preset = conf.get("preset")
    meta = " · ".join(part for part in (provider, preset) if part)
    model = conf.get("model_id") or default
    return f"{model}  ({meta})" if meta else model


def _snapshot_rows(kind: str) -> list[tuple[str, str, str | None]]:
    """Build aligned (label, value, style) rows for a startup kind."""
    from configs.settings import settings

    planner = settings.planner_llm
    worker = settings.tool_llm
    key_env = planner.get("api_key_env") or "API key"
    key_ok = bool(planner.get("api_key_configured"))

    rows: list[tuple[str, str, str | None]] = [
        ("协调器", _llm_line(planner), None),
        ("工作者", _llm_line(worker), None),
        ("密钥", f"{key_env}  已配置" if key_ok else f"{key_env}  缺失", "ok" if key_ok else "warn"),
    ]

    if kind in {"serve", "web"}:
        web = settings.web_runtime
        rows.append(
            ("Web", f"http://{web.get('host', '127.0.0.1')}:{web.get('port', 8765)}", None)
        )
        db = mask_dsn(settings.database_runtime.get("url", "") or "")
        if db:
            rows.append(("数据库", db, None))
        gateway = settings.gateway_runtime
        transport = gateway.get("transport", "in_process")
        redis = gateway.get("redis_url", "")
        rows.append(("传输", f"{transport} · Redis {redis}" if redis else transport, None))
        rows.append(
            ("配额", "启用" if settings.quota_enforcement_enabled else "关闭",
             "warn" if settings.quota_enforcement_enabled else None)
        )
    elif kind in {"monitor", "observe"}:
        monitor = settings.monitor_runtime
        rows.append(
            ("监控", f"http://{monitor.get('host', '127.0.0.1')}:{monitor.get('port', 8766)}", None)
        )
        retention = monitor.get("retention", {})
        if retention.get("enabled", True):
            rows.append(
                ("数据保留",
                 f"trace {retention.get('trace_days', '?')} 天 · event {retention.get('event_days', '?')} 天",
                 None)
            )
        db = mask_dsn(settings.database_runtime.get("url", "") or "")
        if db:
            rows.append(("数据库", db, None))
    elif kind == "worker":
        gateway = settings.gateway_runtime
        redis = gateway.get("redis_url", "")
        rows.append(("角色", "worker（消费 turn 队列）", None))
        rows.append(("传输", f"{gateway.get('transport', 'redis')} · Redis {redis}" if redis else gateway.get("transport", "redis"), None))
    elif kind == "sandbox-manager":
        mode = getattr(settings, "NLP_AGENT_SANDBOX_RUNTIME_MODE", "disabled")
        backend = getattr(settings, "NLP_AGENT_SANDBOX_RUNTIME_BACKEND", "runsc")
        rows.append(("沙箱", f"{mode}  ({backend})", "warn" if mode == "inprocess" else None))
    elif kind == "chat":
        rows.append(("会话", "channel: cli（登录后进入交互）", None))

    return rows


def print_startup_banner(kind: str) -> None:
    """Print the Nova logo and a per-role snapshot; colors only when appropriate."""
    color = _use_color()

    for line in NOVA_LOGO:
        print(_paint(line, "cyan", color))
    print()
    print(
        f"  {_paint('Nova', 'bold', color)} · {_TAGLINE}"
        f"    {_paint('v' + _read_version(), 'dim', color)}"
    )
    print()

    rows = _snapshot_rows(kind)
    label_width = max(_display_width(label) for label, _, _ in rows)
    for label, value, style in rows:
        code = {"ok": "green", "warn": "yellow"}.get(style)
        label_text = _paint(label + " " * (label_width - _display_width(label)), "dim", color)
        value_text = _paint(value, code, color)
        print(f"  {label_text}  {value_text}")
    print()