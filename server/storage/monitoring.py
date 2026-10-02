"""Read-only storage summaries for the current deployment environment."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import make_url

from configs.settings import settings
from server.infrastructure.mysql.models import StorageAccountModel
from server.storage.policy import usage_state


def deployment_environment(settings_obj: Any = settings) -> str:
    """Return the normalized environment label for one running deployment."""

    configured = str(getattr(settings_obj, "NLP_AGENT_DEPLOYMENT_ENV", "") or "").strip().lower()
    if configured in {"test", "testing", "staging"}:
        return "test"
    if configured in {"prod", "production"}:
        return "production"

    # Keep older server .env files useful while still deriving the label from
    # the current process configuration, never by probing sibling databases.
    database_name = database_scope(str(getattr(settings_obj, "NLP_AGENT_DATABASE_URL", "") or ""))["name"].lower()
    if "test" in database_name or "staging" in database_name:
        return "test"
    if "prod" in database_name or "production" in database_name:
        return "production"
    return "unknown"


def database_scope(database_url: str) -> dict[str, str]:
    """Expose only non-secret identity fields for the selected database."""

    try:
        parsed = make_url(database_url.strip())
    except Exception:
        return {"name": "unknown", "host": "unknown"}
    return {
        "name": parsed.database or "unknown",
        "host": parsed.host or "unknown",
    }


def _disk_state(free_bytes: int, minimum_free_bytes: int) -> str:
    if free_bytes <= max(0, minimum_free_bytes):
        return "critical"
    if free_bytes <= max(0, minimum_free_bytes) * 2:
        return "warning"
    return "normal"


def build_storage_snapshot(
    *,
    environment: str,
    database: dict[str, str],
    core_used_bytes: int,
    files_used_bytes: int,
    core_reserved_bytes: int,
    files_reserved_bytes: int,
    account_count: int,
    global_limit_bytes: int,
    disk_total_bytes: int,
    disk_free_bytes: int,
    minimum_free_bytes: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a monitor payload without combining data from another environment."""

    core_used = max(0, int(core_used_bytes))
    files_used = max(0, int(files_used_bytes))
    reserved = max(0, int(core_reserved_bytes)) + max(0, int(files_reserved_bytes))
    account_used = core_used + files_used
    pool_used = account_used + reserved
    limit = max(0, int(global_limit_bytes))
    disk_total = max(0, int(disk_total_bytes))
    disk_free = min(disk_total, max(0, int(disk_free_bytes))) if disk_total else 0
    disk_used = max(0, disk_total - disk_free)
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "generated_at": timestamp,
        "environment": {"code": environment, "label": {"test": "测试", "production": "生产"}.get(environment, "未标记")},
        "scope": "current_environment_only",
        "database": database,
        "account_pool": {
            "used_bytes": account_used,
            "reserved_bytes": reserved,
            "total_bytes": pool_used,
            "limit_bytes": limit,
            "available_bytes": max(0, limit - pool_used),
            "ratio": min(1.0, pool_used / limit) if limit else 1.0,
            "state": usage_state(pool_used, limit),
            "account_count": max(0, int(account_count)),
        },
        "disk": {
            "used_bytes": disk_used,
            "free_bytes": disk_free,
            "total_bytes": disk_total,
            "ratio": min(1.0, disk_used / disk_total) if disk_total else 1.0,
            "state": _disk_state(disk_free, minimum_free_bytes),
            "shared_physical_disk": True,
        },
    }


async def current_storage_snapshot(
    db: AsyncSession,
    *,
    settings_obj: Any = settings,
) -> dict[str, Any]:
    """Read only the ledger and disk visible to the current deployment."""

    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(StorageAccountModel.core_used_bytes), 0),
                func.coalesce(func.sum(StorageAccountModel.files_used_bytes), 0),
                func.coalesce(func.sum(StorageAccountModel.core_reserved_bytes), 0),
                func.coalesce(func.sum(StorageAccountModel.files_reserved_bytes), 0),
                func.count(StorageAccountModel.id),
            ).select_from(StorageAccountModel)
        )
    ).one()
    disk = shutil.disk_usage(settings_obj.BASE_DIR)
    return build_storage_snapshot(
        environment=deployment_environment(settings_obj),
        database=database_scope(str(getattr(settings_obj, "NLP_AGENT_DATABASE_URL", "") or "")),
        core_used_bytes=int(row[0] or 0),
        files_used_bytes=int(row[1] or 0),
        core_reserved_bytes=int(row[2] or 0),
        files_reserved_bytes=int(row[3] or 0),
        account_count=int(row[4] or 0),
        global_limit_bytes=int(settings_obj.NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES),
        disk_total_bytes=disk.total,
        disk_free_bytes=disk.free,
        minimum_free_bytes=int(settings_obj.NLP_AGENT_STORAGE_MIN_FREE_BYTES),
    )
