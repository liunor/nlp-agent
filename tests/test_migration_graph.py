from __future__ import annotations

import importlib
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


def test_migration_graph_has_one_head_after_all_feature_branches_are_merged() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_heads() == ["20260904_49_billable_features"]
    assert scripts.get_revision("20260904_48_developer_merge").down_revision == (
        "20260903_46_remove_dev_sessions",
        "20260903_47_sms_send_locks",
    )
    assert scripts.get_revision("20260903_46_remove_dev_sessions").down_revision == (
        "20260901_45_audit_quota_merge"
    )
    assert scripts.get_revision("20260903_47_sms_send_locks").down_revision == (
        "20260903_46_fixed_role_backfill"
    )
    assert scripts.get_revision("20260903_46_fixed_role_backfill").down_revision == (
        "20260902_45_merge_auth_quota"
    )
    assert scripts.get_revision("20260902_45_merge_auth_quota").down_revision == (
        "20260901_44_quota_summary",
        "20260831_43_auth_code_identity",
    )
    assert scripts.get_revision("20260831_43_auth_code_identity").down_revision == (
        "20260831_42_merge_heads"
    )
    assert scripts.get_revision("20260901_44_quota_summary").down_revision == (
        "20260901_43_role_credit_ops",
        "20260831_40_summary_merge",
    )
    assert scripts.get_revision("20260901_43_role_credit_ops").down_revision == (
        "20260831_42_quota_daily_weekly"
    )
    assert scripts.get_revision("20260830_43_quota_scope_lock").down_revision == (
        "20260830_42_quota_phase4"
    )
    assert scripts.get_revision("20260830_42_quota_phase4").down_revision == (
        "20260830_41_quota_menu"
    )
    assert scripts.get_revision("20260830_41_quota_menu").down_revision == (
        "20260830_40_quota_phase3"
    )
    assert scripts.get_revision("20260831_39_feedback_student").down_revision == (
        "20260831_38_feedback_write"
    )
    assert scripts.get_revision("20260831_40_summary_merge").down_revision == (
        "20260831_39_feedback_student",
        "20260831_39_summary_backoff",
    )
    assert scripts.get_revision("20260831_39_feedback_student").down_revision == "20260831_38_feedback_write"
    assert scripts.get_revision("20260831_38_feedback_write").down_revision == "20260831_37_feedback_meta"
    assert scripts.get_revision("20260831_37_feedback_meta").down_revision == "20260829_36_usage_indexes"
    assert scripts.get_revision("20260831_39_summary_backoff").down_revision == "20260830_38_session_title_manual"
    assert scripts.get_revision("20260830_38_session_title_manual").down_revision == "20260829_37_session_summary"
    assert scripts.get_revision("20260829_37_session_summary").down_revision == "20260829_36_usage_indexes"
    assert scripts.get_revision("20260829_36_usage_indexes").down_revision == "20260829_35_user_mgmt_menus"
    assert scripts.get_revision("20260829_35_user_mgmt_menus").down_revision == "20260828_34_auth_codes"
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
