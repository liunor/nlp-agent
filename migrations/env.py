from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from dotenv import load_dotenv

from server.infrastructure.mysql.base import Base
from server.infrastructure.mysql import models  # noqa: F401
from server.quota import models as quota_models  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ``alembic`` is commonly invoked directly from the project root.  In that
# mode pydantic-settings is not involved, so load the same local configuration
# that the application uses before reading the database URL.  Existing process
# environment values still win over the local file.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

if database_url := os.getenv("NLP_AGENT_DATABASE_URL"):
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
