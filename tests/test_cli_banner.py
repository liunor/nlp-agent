import configs.settings
from types import SimpleNamespace

from core.cli_banner import (
    NOVA_LOGO,
    _display_width,
    _snapshot_rows,
    mask_dsn,
    print_startup_banner,
)


def make_settings(**overrides):
    """Build a lightweight settings substitute for snapshot assertions."""
    base = SimpleNamespace(
        planner_llm={
            "model_id": "deepseek-chat",
            "provider": "deepseek",
            "preset": None,
            "api_key_env": "DEEPSEEK_API_KEY",
            "api_key_configured": True,
        },
        tool_llm={"model_id": "qwen-tool", "provider": "qwen", "preset": "fast"},
        web_runtime={"host": "127.0.0.1", "port": 8765},
        database_runtime={
            "url": "mysql+aiomysql://alice:s3cr3t@db.example.com:3306/nlp_agent?charset=utf8mb4"
        },
        gateway_runtime={
            "transport": "redis",
            "redis_url": "redis://:r3d1s@redis.example.com:6379/0",
        },
        monitor_runtime={
            "host": "127.0.0.1",
            "port": 8766,
            "retention": {"enabled": True, "trace_days": 7, "event_days": 30},
        },
        quota_enforcement_enabled=True,
        NLP_AGENT_SANDBOX_RUNTIME_MODE="gvisor",
        NLP_AGENT_SANDBOX_RUNTIME_BACKEND="runsc",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_nova_logo_shape():
    assert len(NOVA_LOGO) == 6
    assert all(line.strip() and not line.endswith(" ") for line in NOVA_LOGO)


def test_mask_dsn_hides_password():
    dsn = "mysql+aiomysql://alice:s3cr3t@db.example.com:3306/nlp_agent?charset=utf8mb4"
    masked = mask_dsn(dsn)
    assert "s3cr3t" not in masked
    assert ":***@" in masked
    assert "mysql+aiomysql://" in masked
    assert "db.example.com:3306" in masked
    assert "/nlp_agent" in masked


def test_mask_dsn_hides_redis_password():
    masked = mask_dsn("redis://:s3cr3t@redis.example.com:6379/0")
    assert "s3cr3t" not in masked
    assert ":***@" in masked
    assert "redis.example.com:6379" in masked


def test_mask_dsn_hides_query_credentials():
    dsn = (
        "mysql://alice:pw@db.example.com:3306/nlp?"
        "password=supersecret&token=abc123&charset=utf8mb4"
    )
    masked = mask_dsn(dsn)
    assert "supersecret" not in masked
    assert "abc123" not in masked
    assert "password=***" in masked
    assert "token=***" in masked
    assert "charset=utf8mb4" in masked


def test_mask_dsn_no_credentials():
    assert mask_dsn("sqlite:///x.db") == "sqlite:///x.db"
    assert mask_dsn("") == ""


def test_display_width_counts_cjk_and_skips_zero_width():
    assert _display_width("中文") == 4
    assert _display_width("abc") == 3
    assert _display_width("e\u0301") == 1  # e + combining acute accent
    assert _display_width("\u200d") == 0  # zero-width joiner
    assert _display_width("a\ufe0f") == 1  # variation selector ignored


def test_snapshot_serve_row_labels():
    rows = _snapshot_rows("serve", make_settings())
    labels = [label for label, _, _ in rows]
    for expected in ("协调器", "工作者", "密钥", "Web", "数据库", "传输", "配额"):
        assert expected in labels


def test_snapshot_masks_database_and_redis_passwords():
    rows = dict((label, value) for label, value, _ in _snapshot_rows("serve", make_settings()))
    assert "s3cr3t" not in rows["数据库"]
    assert "***" in rows["数据库"]
    assert "r3d1s" not in rows["传输"]
    assert "***" in rows["传输"]


def test_snapshot_monitor_rows():
    rows = dict((label, value) for label, value, _ in _snapshot_rows("monitor", make_settings()))
    assert rows["监控"] == "http://127.0.0.1:8766"
    assert "trace 7 天" in rows["数据保留"]
    assert "event 30 天" in rows["数据保留"]
    assert "***" in rows["数据库"]


def test_snapshot_worker_rows():
    rows = dict((label, value) for label, value, _ in _snapshot_rows("worker", make_settings()))
    assert rows["角色"] == "worker（消费 turn 队列）"
    assert "redis" in rows["传输"]
    assert "***" in rows["传输"]


def test_snapshot_sandbox_rows():
    rows = dict((label, value) for label, value, _ in _snapshot_rows("sandbox-manager", make_settings()))
    assert rows["沙箱"] == "gvisor  (runsc)"


def test_banner_plaintext_without_color(monkeypatch, capsys):
    monkeypatch.setattr(configs.settings, "settings", make_settings())
    monkeypatch.setenv("NO_COLOR", "1")
    print_startup_banner("chat")
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "Nova" in out


def test_banner_snapshot_is_boxed(monkeypatch, capsys):
    monkeypatch.setattr(configs.settings, "settings", make_settings())
    monkeypatch.setenv("NO_COLOR", "1")
    print_startup_banner("chat")
    out = capsys.readouterr().out
    assert "┌" in out and "└" in out
    # Every box side aligns: each border line starts with a "│" gutter.
    assert "│" in out
