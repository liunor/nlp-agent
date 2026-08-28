from __future__ import annotations

import importlib
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_graph_has_one_head_after_knowledge_book_is_added() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_heads() == ["20260828_34_auth_codes"]
    assert scripts.get_revision("20260828_34_auth_codes").down_revision == "20260828_33_user_phone"
    assert scripts.get_revision("20260828_33_user_phone").down_revision == "20260827_32_book_merge"
    assert scripts.get_revision("20260827_32_book_merge").down_revision == (
        "20260826_29",
        "20260827_31_book_assets",
    )


def test_migration_revision_ids_fit_alembic_version_column() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert all(len(revision.revision) <= 32 for revision in scripts.walk_revisions())


def test_knowledge_book_page_text_columns_have_no_mysql_default() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260825_25_knowledge_book_pages"
    )
    tables: list[sa.Table] = []

    def capture_table(name: str, *columns: sa.Column, **kwargs: object) -> None:
        tables.append(sa.Table(name, sa.MetaData(), *columns))

    migration.op = SimpleNamespace(create_table=capture_table)
    migration.upgrade()

    draft_markdown = tables[0].c.draft_markdown
    assert draft_markdown.server_default is None
