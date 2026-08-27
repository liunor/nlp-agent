from __future__ import annotations

import importlib
from types import SimpleNamespace


def test_sandbox_developer_menu_migration_inserts_complete_menu_row(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260825_27_sandbox_developer_menu"
    )
    inserts: list[tuple[object, list[dict[str, object]]]] = []

    class Result:
        def first(self):
            return None

    class Bind:
        def execute(self, statement):
            return Result()

    monkeypatch.setattr(
        migration,
        "context",
        SimpleNamespace(is_offline_mode=lambda: False),
    )
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: Bind(),
            bulk_insert=lambda table, rows: inserts.append((table, rows)),
        ),
    )

    migration.upgrade()

    menu_table, menu_rows = inserts[0]
    assert set(menu_table.c.keys()) == {
        "id",
        "parent_id",
        "menu_type",
        "name",
        "route_path",
        "component_key",
        "permission_id",
        "client_scope",
        "sort_order",
        "visible",
        "status",
    }
    assert set(menu_rows[0]) == set(menu_table.c.keys())
