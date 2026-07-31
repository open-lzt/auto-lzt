"""Alembic async env — wired to lzt-flow's Base metadata and Settings DSN."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from alembic import context
from app.core.config import get_settings
from app.db import models  # noqa: F401 — registers ORM tables on Base.metadata
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata

# Alembic's default `alembic_version.version_num` is VARCHAR(32), and this project names revisions
# after what they do — `0011_account_profile_and_run_error` is 34 characters. Postgres refuses the
# write and the upgrade dies mid-chain; SQLite ignores the limit, which is why every local run and
# every test that built the schema with `create_all` stayed green while `upgrade head` had in fact
# never worked on Postgres. No Postgres database can hold a revision past 0010, since stamping one
# is the step that fails — so widening the column here is enough, with no ALTER for existing rows.
_VERSION_COLUMN = sa.String(255)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_column_type=_VERSION_COLUMN,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: object) -> None:
    context.configure(  # type: ignore[call-overload]
        connection=connection,
        target_metadata=target_metadata,
        version_table_column_type=_VERSION_COLUMN,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
