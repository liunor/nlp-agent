"""backfill account storage ledgers and add quota-change audit"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME


revision = "20260927_43_storage_quota_backfill"
down_revision = "20260927_42_storage_quota"
branch_labels = None
depends_on = None


def _add_grouped(bind, query: str) -> None:
    for row in bind.execute(sa.text(query)).mappings():
        bind.execute(
            sa.text(
                "UPDATE nlp_storage_accounts "
                "SET core_used_bytes=core_used_bytes+:amount "
                "WHERE owner_user_id=:owner"
            ),
            {"owner": row["owner"], "amount": int(row["amount"] or 0)},
        )


def _add_files_grouped(bind, query: str) -> None:
    for row in bind.execute(sa.text(query)).mappings():
        bind.execute(
            sa.text(
                "UPDATE nlp_storage_accounts "
                "SET files_used_bytes=files_used_bytes+:amount "
                "WHERE owner_user_id=:owner"
            ),
            {"owner": row["owner"], "amount": int(row["amount"] or 0)},
        )


def _add_upload_bytes(bind) -> None:
    configured = os.environ.get("NLP_AGENT_UPLOADS_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / ".data" / "uploads"
    if not root.is_dir():
        return
    for workspace_root in root.iterdir():
        if not workspace_root.is_dir():
            continue
        for owner_root in workspace_root.iterdir():
            if not owner_root.is_dir():
                continue
            amount = 0
            for path in owner_root.rglob("*"):
                if path.is_file():
                    try:
                        amount += path.stat().st_size
                    except OSError:
                        continue
            if amount:
                bind.execute(
                    sa.text(
                        "UPDATE nlp_storage_accounts "
                        "SET core_used_bytes=core_used_bytes+:amount "
                        "WHERE owner_user_id=:owner"
                    ),
                    {"owner": owner_root.name, "amount": amount},
                )


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = sa.String(36, collation="ascii_bin")
    op.create_table(
        "nlp_storage_quota_audits",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("actor_user_id", uuid_type, nullable=False),
        sa.Column("target_user_id", uuid_type, nullable=False),
        sa.Column("previous_values", sa.JSON(), nullable=False),
        sa.Column("new_values", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False, server_default=sa.text("UTC_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["nlp_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["nlp_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nlp_storage_quota_audits_target_created",
        "nlp_storage_quota_audits",
        ["target_user_id", "created_at"],
    )
    op.create_index(
        "ix_nlp_storage_quota_audits_actor_created",
        "nlp_storage_quota_audits",
        ["actor_user_id", "created_at"],
    )

    bind.execute(
        sa.text(
            "INSERT INTO nlp_storage_accounts(id,owner_user_id) "
            "SELECT UUID(),id FROM nlp_users "
            "ON DUPLICATE KEY UPDATE owner_user_id=VALUES(owner_user_id)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE nlp_storage_accounts SET core_used_bytes=0,files_used_bytes=0,last_reconciled_at=NULL"
        )
    )

    _add_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(COALESCE(title,''))+OCTET_LENGTH(id)),0) AS amount "
        "FROM nlp_conversations GROUP BY owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT user_id AS owner, COALESCE(SUM(OCTET_LENGTH(COALESCE(input_text,''))+OCTET_LENGTH(COALESCE(result_text,''))+OCTET_LENGTH(COALESCE(error_message,''))+OCTET_LENGTH(CAST(COALESCE(learning_state_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_turns GROUP BY user_id",
    )
    _add_grouped(
        bind,
        "SELECT c.owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(COALESCE(m.content,''))),0) AS amount "
        "FROM nlp_conversation_messages m JOIN nlp_conversations c ON c.id=m.conversation_id GROUP BY c.owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT t.user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(e.payload_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_turn_events e JOIN nlp_turns t ON t.id=e.turn_id GROUP BY t.user_id",
    )
    _add_grouped(
        bind,
        "SELECT c.owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(t.content_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.tool_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.usage_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_conversation_transcripts t JOIN nlp_conversations c ON c.id=t.session_id GROUP BY c.owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(content_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_memory_documents GROUP BY user_id",
    )
    _add_grouped(
        bind,
        "SELECT user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(payload_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_memory_archives GROUP BY user_id",
    )
    _add_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(checkpoint_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(metadata_json,'{}') AS CHAR))),0) AS amount "
        "FROM nlp_agent_checkpoints GROUP BY owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(checkpoint_blob)+OCTET_LENGTH(metadata_blob)),0) AS amount "
        "FROM nlp_langgraph_checkpoints GROUP BY owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(value_blob)),0) AS amount "
        "FROM nlp_langgraph_checkpoint_blobs GROUP BY owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(OCTET_LENGTH(value_blob)),0) AS amount "
        "FROM nlp_langgraph_checkpoint_writes GROUP BY owner_user_id",
    )
    _add_grouped(
        bind,
        "SELECT t.user_id AS owner, COALESCE(SUM(OCTET_LENGTH(CAST(COALESCE(tc.request_json,'') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(tc.result_json,'') AS CHAR))),0) AS amount "
        "FROM nlp_tool_calls tc JOIN nlp_turns t ON t.id=tc.turn_id GROUP BY t.user_id",
    )
    _add_upload_bytes(bind)
    _add_files_grouped(
        bind,
        "SELECT owner_user_id AS owner, COALESCE(SUM(size_bytes),0) AS amount "
        "FROM nlp_user_files WHERE kind='file' AND status IN ('active','trashed') GROUP BY owner_user_id",
    )
    bind.execute(sa.text("UPDATE nlp_storage_accounts SET last_reconciled_at=UTC_TIMESTAMP(6)"))


def downgrade() -> None:
    op.drop_index("ix_nlp_storage_quota_audits_actor_created", table_name="nlp_storage_quota_audits")
    op.drop_index("ix_nlp_storage_quota_audits_target_created", table_name="nlp_storage_quota_audits")
    op.drop_table("nlp_storage_quota_audits")
