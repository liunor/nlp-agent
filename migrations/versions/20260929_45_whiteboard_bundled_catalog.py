"""move the bundled Excalidraw catalog into the shared library"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

from gateway.whiteboard_bundled import load_bundled_whiteboard_items


revision = "20260929_45_wb_catalog"
down_revision = ("20260927_42_whiteboard_asset_codes", "20260927_45_merge_storage_heads")
branch_labels = None
depends_on = None

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS nlp_whiteboard_library_catalog_meta ("
            "catalog_key VARCHAR(128) NOT NULL PRIMARY KEY, "
            "seeded_at DATETIME(6) NOT NULL DEFAULT UTC_TIMESTAMP(6))"
        )
    )
    items = load_bundled_whiteboard_items()
    bind.execute(
        sa.text(
            "INSERT IGNORE INTO nlp_whiteboard_library_catalog_meta(catalog_key) "
            "VALUES ('vendored-excalidraw-v1')"
        )
    )
    for item in items:
        exists = bind.execute(
            sa.text("SELECT 1 FROM nlp_whiteboard_library_items WHERE id=:id"),
            {"id": item["id"]},
        ).first()
        if exists is not None:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO nlp_whiteboard_library_items "
                "(id,asset_code,name,item_json,created_by,created_at,updated_at) "
                "VALUES (:id,:asset_code,:name,:item_json,:created_by,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))"
            ),
            {
                "id": item["id"],
                "asset_code": item["asset_code"],
                "name": item["name"],
                "item_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                "created_by": _SYSTEM_USER_ID,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    ids = [item["id"] for item in load_bundled_whiteboard_items()]
    if not ids:
        return
    bind.execute(
        sa.text("DELETE FROM nlp_whiteboard_library_items WHERE id IN (" + ",".join(f":id{i}" for i in range(len(ids))) + ")"),
        {f"id{i}": item_id for i, item_id in enumerate(ids)},
    )
    op.drop_table("nlp_whiteboard_library_catalog_meta")
