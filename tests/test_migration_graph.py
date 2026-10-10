from __future__ import annotations

import importlib
from io import StringIO
import re
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import command
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from core.rbac import Permission
from server.infrastructure.mysql.models import AuthCodeModel
from server.rbac.catalog import permission_id, permission_row, role_id

def test_migration_graph_has_one_head_after_all_feature_branches_are_merged() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_heads() == ["20261010_48_cleanup_index"]
    assert scripts.get_revision("20261009_47_transfer_notify_seq").down_revision == "20261009_46_file_transfers"
    assert scripts.get_revision("20261009_46_file_transfers").down_revision == "20261009_46_storage_repair"
    assert scripts.get_revision("20261009_46_storage_repair").down_revision == "20261001_48_account_delete_self"
    assert scripts.get_revision("20260929_45_wb_catalog").down_revision == (
        "20260927_42_wb_asset_codes",
        "20260927_45_merge_storage_heads",
    )
    assert scripts.get_revision("20260927_45_merge_storage_heads").down_revision == (
        "20260920_58_knowledge_book_files",
        "20260927_44_storage_pool_lock",
    )
    assert scripts.get_revision("20260927_42_wb_asset_codes").down_revision == "20260910_53_whiteboard_library"
    assert scripts.get_revision("20260910_53_whiteboard_library") is not None
    assert scripts.get_revision("20260920_58_knowledge_book_files") is not None


def test_current_head_revision_fits_legacy_alembic_version_column() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(scripts.get_heads()) == 1
    assert len(scripts.get_heads()[0]) <= 32


@pytest.mark.parametrize(
    "revision",
    [
        "20260831_39_feedback_student",
        "20260910_53_whiteboard_library",
        "20260927_45_merge_storage_heads",
    ],
)
def test_historical_upgrade_entry_points_resolve_to_the_current_head(revision: str) -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    path = list(scripts.iterate_revisions("20261010_48_cleanup_index", revision))

    assert path
    assert path[0].revision == "20261010_48_cleanup_index"


def test_migration_revision_ids_fit_the_expanded_alembic_version_column() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert all(len(revision.revision) <= 64 for revision in scripts.walk_revisions())


def test_storage_quota_migration_expands_the_alembic_version_column() -> None:
    migration = importlib.import_module("migrations.versions.20260927_42_storage_quota")
    alterations: list[tuple[str, str, int | None]] = []

    def capture_alter(table: str, column: str, **kwargs: object) -> None:
        type_ = kwargs.get("type_")
        alterations.append((table, column, getattr(type_, "length", None)))

    original_op = migration.op
    migration.op = SimpleNamespace(alter_column=capture_alter)
    try:
        migration._expand_alembic_version_column()
    finally:
        migration.op = original_op

    assert alterations == [("alembic_version", "version_num", 64)]


def test_complete_offline_migration_chain_compiles() -> None:
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE nlp_users" in sql
    for value in (
        "agent:session:read",
        "learning:feedback:write",
        "quota:usage:read_self",
        "system:quota:read",
        "system:quota:manage",
        "system:runtime:reset",
        "/developer/quotas",
    ):
        assert value in sql
    permission_inserts = re.findall(
        r"INSERT INTO nlp_permissions \([^;]+?;", sql, flags=re.DOTALL
    )
    for permission_code in (
        "agent:session:read",
        "quota:usage:read_self",
        "system:quota:read",
        "system:quota:manage",
        "system:runtime:reset",
    ):
        assert sum(permission_code in statement for statement in permission_inserts) == 1
    menu_inserts = re.findall(r"INSERT INTO nlp_menus \([^;]+?;", sql, flags=re.DOTALL)
    assert sum("/developer/quotas" in statement for statement in menu_inserts) == 1


def test_transfer_cleanup_index_migration_is_safe_to_retry_after_partial_ddl() -> None:
    migration = importlib.import_module(
        "migrations.versions.20261010_48_transfer_cleanup_index"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "nlp_file_transfers",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("status", sa.String()),
        sa.Column("created_at", sa.DateTime()),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE INDEX ix_nlp_file_transfers_cleanup_status_created "
                "ON nlp_file_transfers (status, created_at, id)"
            )
        )
        migration_context = MigrationContext.configure(connection)
        migration.op = Operations(migration_context)
        migration.context = SimpleNamespace(is_offline_mode=lambda: False)

        migration.upgrade()

        assert {
            index["name"]
            for index in sa.inspect(connection).get_indexes("nlp_file_transfers")
        } == {"ix_nlp_file_transfers_cleanup_status_created"}


def test_obsolete_rbac_menu_cleanup_removes_only_retired_entries() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    menus = sa.Table(
        "nlp_menus",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("parent_id", sa.String()),
        sa.Column("menu_type", sa.String()),
        sa.Column("name", sa.String()),
        sa.Column("route_path", sa.String()),
        sa.Column("component_key", sa.String()),
        sa.Column("permission_id", sa.String()),
        sa.Column("client_scope", sa.String()),
        sa.Column("sort_order", sa.Integer()),
        sa.Column("visible", sa.Boolean()),
        sa.Column("status", sa.String()),
    )
    role_menus = sa.Table(
        "nlp_role_menus",
        metadata,
        sa.Column("role_id", sa.String(), primary_key=True),
        sa.Column("menu_id", sa.String(), primary_key=True),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "migrations.versions.20260916_57_rbac_menu_cleanup"
    )
    retired_sandbox_id = "e9aaed99-d16c-5723-afeb-09a9487767d2"

    with engine.begin() as connection:
        connection.execute(
            menus.insert(),
            [
                {
                    "id": retired_sandbox_id,
                    "name": "代码沙箱",
                    "route_path": "/developer/sandbox",
                },
                {"id": "active-menu", "name": "工作台", "route_path": "/developer"},
            ],
        )
        connection.execute(
            role_menus.insert(),
            [
                {"role_id": "developer", "menu_id": retired_sandbox_id},
                {"role_id": "developer", "menu_id": "active-menu"},
            ],
        )
        migration_context = MigrationContext.configure(connection)
        migration.op = Operations(migration_context)

        migration.upgrade()

        assert set(connection.execute(sa.select(menus.c.id)).scalars()) == {
            "active-menu"
        }
        assert set(connection.execute(sa.select(role_menus.c.menu_id)).scalars()) == {
            "active-menu"
        }

        migration.downgrade()

        restored = connection.execute(
            sa.select(menus).where(menus.c.id == retired_sandbox_id)
        ).mappings().one()
        assert restored["route_path"] == "/developer/sandbox"
        assert restored["client_scope"] == "developer"
        assert connection.execute(
            sa.select(role_menus.c.role_id).where(
                role_menus.c.menu_id == retired_sandbox_id
            )
        ).scalar_one() == "ddff0b44-2694-540f-a808-6f4d1827e413"


def test_auth_code_subject_accepts_full_length_email_addresses() -> None:
    assert AuthCodeModel.__table__.c.subject.type.length == 254


def test_email_registration_migration_expands_auth_code_subject(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260914_55_email_registration"
    )
    alterations: list[tuple[str, str, dict[str, object]]] = []
    fake_op = SimpleNamespace(
        alter_column=lambda table, column, **kwargs: alterations.append(
            (table, column, kwargs)
        ),
        execute=lambda statement: None,
    )
    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(
        migration, "context", SimpleNamespace(is_offline_mode=lambda: False)
    )
    monkeypatch.setattr(migration, "_user_columns", lambda: {"email", "email_normalized"})
    monkeypatch.setattr(
        migration,
        "_has_table",
        lambda name: name
        in {"nlp_auth_codes", "nlp_email_send_audits", "nlp_email_send_locks"},
    )

    migration.upgrade()

    assert len(alterations) == 1
    table, column, options = alterations[0]
    assert (table, column) == ("nlp_auth_codes", "subject")
    assert options["existing_type"].length == 64
    assert options["type_"].length == 254
    assert options["existing_nullable"] is False


def test_usage_cache_backfill_repairs_only_provable_legacy_facts(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260912_54_usage_cache_attribution_backfill"
    )
    added_columns: list[tuple[str, sa.Column]] = []
    statements: list[str] = []
    fake_op = SimpleNamespace(
        add_column=lambda table, column: added_columns.append((table, column)),
        execute=lambda statement: statements.append(str(statement)),
    )
    monkeypatch.setattr(migration, "op", fake_op)
    monkeypatch.setattr(migration, "_usage_columns", lambda: set())

    migration.upgrade()

    assert added_columns[0][0] == "nlp_usage_events"
    assert added_columns[0][1].name == "cache_status"
    sql = "\n".join(statements)
    assert "usage_source = 'provider'" in sql
    assert "cache_miss_input_tokens > 0" in sql
    assert "preset LIKE 'coordinator-%'" in sql
    assert "preset LIKE 'worker-%'" in sql
    assert "nlp_observability_records" in sql
    assert "$.payload.attributes.operation_id" in sql
    assert "$.payload.worker_id" in sql
    assert "route = 'utility'" in sql
    assert "route = 'vision-worker'" in sql
    assert "WHERE purpose <> 'worker'" in sql
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


def test_quota_daily_weekly_migration_renames_legacy_monthly_rows() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "nlp_quota_policies",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monthly_limit_micro", sa.BigInteger()),
    )
    for table_name in (
        "nlp_quota_buckets",
        "nlp_quota_grants",
        "nlp_quota_adjustments",
        "nlp_quota_credit_operations",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("bucket_type", sa.String(), nullable=False),
        )
    metadata.create_all(engine)

    with engine.begin() as connection:
        for table_name in (
            "nlp_quota_buckets",
            "nlp_quota_grants",
            "nlp_quota_adjustments",
            "nlp_quota_credit_operations",
        ):
            connection.execute(
                metadata.tables[table_name].insert().values(
                    id=table_name, bucket_type="monthly"
                )
            )
        migration_context = MigrationContext.configure(connection)
        migration = importlib.import_module(
            "migrations.versions.20260831_42_quota_daily_weekly"
        )
        migration.op = Operations(migration_context)
        migration.context = SimpleNamespace(is_offline_mode=lambda: False)

        migration.upgrade()

        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("nlp_quota_policies")
        }
        assert "weekly_limit_micro" in columns
        assert "monthly_limit_micro" not in columns
        for table_name in (
            "nlp_quota_buckets",
            "nlp_quota_grants",
            "nlp_quota_adjustments",
            "nlp_quota_credit_operations",
        ):
            assert connection.execute(
                sa.select(metadata.tables[table_name].c.bucket_type)
            ).scalar_one() == "weekly"


def test_phone_schema_repair_adds_missing_normalized_column_and_backfills() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    users = sa.Table(
        "nlp_users",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("phone_number", sa.String(20)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "migrations.versions.20260905_49_repair_user_phone_schema_drift"
    )
    with engine.begin() as connection:
        connection.execute(
            users.insert().values(
                id="user-1",
                phone_number="138 0013 8000",
                created_at=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        migration_context = MigrationContext.configure(connection)
        operations = Operations(migration_context)

        migration.op = SimpleNamespace(
            get_bind=operations.get_bind,
            add_column=operations.add_column,
            create_index=operations.create_index,
            create_unique_constraint=lambda name, table, columns: connection.exec_driver_sql(
                f'CREATE UNIQUE INDEX "{name}" ON "{table}" ("{columns[0]}")'
            ),
        )
        migration.context = SimpleNamespace(is_offline_mode=lambda: False)

        migration.upgrade()

        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("nlp_users")
        }
        assert "phone_number_normalized" in columns
        assert connection.execute(
            sa.text(
                "SELECT phone_number_normalized FROM nlp_users WHERE id='user-1'"
            )
        ).scalar_one() == "+8613800138000"


def test_guest_agent_permission_migration_repairs_legacy_role_projection() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    permissions = sa.Table(
        "nlp_permissions",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("domain_name", sa.String()),
        sa.Column("resource_name", sa.String()),
        sa.Column("action_name", sa.String()),
        sa.Column("name", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("is_builtin", sa.Boolean()),
    )
    role_permissions = sa.Table(
        "nlp_role_permissions",
        metadata,
        sa.Column("role_id", sa.String(), primary_key=True),
        sa.Column("permission_id", sa.String(), primary_key=True),
    )
    role_scopes = sa.Table(
        "nlp_role_permission_scopes",
        metadata,
        sa.Column("role_id", sa.String(), primary_key=True),
        sa.Column("permission_id", sa.String(), primary_key=True),
        sa.Column("scope_type", sa.String(), primary_key=True),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "migrations.versions.20260916_56_guest_agent_perms"
    )
    expected_scopes = {
        "agent:session:create": "own",
        "agent:session:read": "workspace",
        "agent:session:update": "own",
        "agent:session:delete": "own",
        "agent:turn:submit": "workspace",
        "agent:turn:cancel": "own",
        "agent:event:replay": "workspace",
    }

    with engine.begin() as connection:
        read_permission = permission_row(Permission.AGENT_SESSION_READ)
        guest_role = role_id("guest")
        connection.execute(permissions.insert().values(**read_permission))
        connection.execute(
            role_permissions.insert().values(
                role_id=guest_role,
                permission_id=read_permission["id"],
            )
        )
        connection.execute(
            role_scopes.insert().values(
                role_id=guest_role,
                permission_id=read_permission["id"],
                scope_type="own",
            )
        )
        # A different built-in role must keep its independent grant and scope.
        connection.execute(
            role_permissions.insert().values(
                role_id=role_id("student"),
                permission_id=read_permission["id"],
            )
        )
        migration_context = MigrationContext.configure(connection)
        migration.op = Operations(migration_context)
        migration.context = SimpleNamespace(is_offline_mode=lambda: False)
        migration.upgrade()
        migration.upgrade()

        guest_role_id = role_id("guest")
        for code, scope in expected_scopes.items():
                permission_value = connection.execute(
                    sa.select(permissions.c.id).where(permissions.c.code == code)
                ).scalar_one()
                assert connection.execute(
                    sa.select(role_permissions.c.permission_id).where(
                        role_permissions.c.role_id == guest_role_id,
                        role_permissions.c.permission_id == permission_value,
                    )
                ).scalar_one() == permission_value
                assert connection.execute(
                    sa.select(role_scopes.c.scope_type).where(
                        role_scopes.c.role_id == guest_role_id,
                        role_scopes.c.permission_id == permission_value,
                    )
                ).scalar_one() == scope

        assert connection.execute(
            sa.select(sa.func.count()).select_from(role_permissions).where(
                role_permissions.c.role_id == guest_role_id
            )
        ).scalar_one() == len(expected_scopes)

        read_permission_id = permission_id(Permission.AGENT_SESSION_READ)
        assert connection.execute(
            sa.select(role_scopes.c.scope_type).where(
                role_scopes.c.role_id == guest_role,
                role_scopes.c.permission_id == read_permission_id,
            )
        ).scalar_one() == "workspace"
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM nlp_rbac_guest_agent_perm_backups"
            )
        ).scalar_one() == len(expected_scopes)

        migration.downgrade()

        assert connection.execute(
            sa.select(role_scopes.c.scope_type).where(
                role_scopes.c.role_id == guest_role,
                role_scopes.c.permission_id == read_permission_id,
            )
        ).scalar_one() == "own"
        assert connection.execute(
            sa.select(sa.func.count()).select_from(role_permissions).where(
                role_permissions.c.role_id == guest_role
            )
        ).scalar_one() == 1
        assert connection.execute(
            sa.select(sa.func.count()).select_from(role_permissions).where(
                role_permissions.c.role_id == role_id("student"),
                role_permissions.c.permission_id == read_permission_id,
            )
        ).scalar_one() == 1
        assert connection.execute(
            sa.select(sa.func.count()).select_from(permissions)
        ).scalar_one() == 1
        assert "nlp_rbac_guest_agent_perm_backups" not in sa.inspect(connection).get_table_names()


def test_guest_agent_permission_migration_emits_offline_seed_sql() -> None:
    from io import StringIO

    migration = importlib.import_module(
        "migrations.versions.20260916_56_guest_agent_perms"
    )
    output = StringIO()
    migration_context = MigrationContext.configure(
        dialect_name="sqlite",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(migration_context)
    migration.context = SimpleNamespace(is_offline_mode=lambda: True)

    migration.upgrade()

    sql = output.getvalue()
    assert "CREATE TABLE nlp_rbac_guest_agent_perm_backups" in sql
    assert "INSERT INTO nlp_permissions" not in sql
    assert "INSERT INTO nlp_role_permissions" not in sql
    assert "INSERT INTO nlp_role_permission_scopes" not in sql
    assert "INSERT INTO nlp_rbac_guest_agent_perm_backups" in sql
