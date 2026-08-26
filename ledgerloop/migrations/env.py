"""Alembic environment.

The connection string comes from DATABASE_URL at runtime, never from alembic.ini,
so no credentials are committed and the same migrations run on host and in Docker.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ledgerloop.config import database_url
from ledgerloop.db import CONNECT_TIMEOUT
from ledgerloop.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", database_url())

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, and that default is wrong here.
    #
    # These migrations no longer run only from a terminal. `api/main.py` applies them
    # inside the application's lifespan, which means fileConfig() runs *after* uvicorn
    # has built its loggers -- and the default switches every one of them off for the
    # rest of the process. The server keeps serving and goes silent: no "application
    # startup complete", no request log, and not even the line api/main.py writes to
    # report whether these migrations succeeded.
    #
    # It cost a misdiagnosis to find. A start-up hang had just been fixed, the fix was
    # verified in isolation, and the server still showed nothing after "Waiting for
    # application startup" -- which read as "still hanging" when it was in fact serving
    # in ten milliseconds. The instrument had been switched off by the thing it was
    # watching.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply the migrations against a real connection.

    THE CONNECT TIMEOUT IS THE SAME ONE THE APPLICATION USES, AND IT HAS TO BE

    This is the second engine in the project. `ledgerloop/db.py` bounds its connect
    because a half-open peer would otherwise hang every caller; this one was built
    without `connect_args` and inherited none of that, which mattered more here than
    there. The application starts by running these migrations inside the lifespan, so an
    unbounded connect in this function does not slow the service down -- it stops the
    service from ever starting, before a single route is mounted.

    Nothing raises while that happens, so the try/except that makes a migration failure
    non-fatal never runs. A migration that can hang is not a non-fatal migration.

    Measured, on Windows, against a port with nothing listening on it: no
    `connect_args` and `connect()` had not returned after ninety seconds, sitting in
    psycopg's connect loop, while a plain blocking socket to the same port was refused in
    two. The deadline lives in `connect_timeout` and in no other place -- libpq is never
    told to give up without it.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": CONNECT_TIMEOUT},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
