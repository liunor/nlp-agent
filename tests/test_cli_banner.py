from core.cli_banner import NOVA_LOGO, _snapshot_rows, mask_dsn, print_startup_banner


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


def test_mask_dsn_no_credentials():
    assert mask_dsn("sqlite:///x.db") == "sqlite:///x.db"
    assert mask_dsn("") == ""


def test_snapshot_serve_row_labels():
    rows = _snapshot_rows("serve")
    labels = [label for label, _, _ in rows]
    for expected in ("协调器", "工作者", "密钥", "Web", "传输", "配额"):
        assert expected in labels


def test_snapshot_masks_database_password():
    rows = _snapshot_rows("serve")
    db_rows = [value for label, value, _ in rows if label == "数据库"]
    for value in db_rows:
        assert "***" in value


def test_banner_plaintext_without_color(monkeypatch, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    print_startup_banner("chat")
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "Nova" in out


def test_banner_snapshot_is_boxed(monkeypatch, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    print_startup_banner("chat")
    out = capsys.readouterr().out
    assert "┌" in out and "└" in out
    # Every box side aligns: each border line starts with a "│" gutter.
    assert "│" in out