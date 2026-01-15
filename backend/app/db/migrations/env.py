"""Alembic environment wiring for Postgres metadata migrations."""

import os
import sys
from logging.config import fileConfig

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run Alembic migrations in offline mode.

    Business purpose:
        Generate migration SQL without connecting to a database.
    Why it exists:
        Supports environments where a live DB connection is unavailable.
    Where used:
        Alembic CLI offline migration commands.
    Inputs:
        None; uses configured database_url from settings.
    Returns:
        None; executes migration scripts in offline mode.
    """
    settings = get_settings()
    url = settings.database_url
    # Configure Alembic with a URL-only context for offline SQL generation.
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run Alembic migrations against a live database connection.

    Business purpose:
        Apply schema changes directly to the configured database.
    Why it exists:
        Ensures migrations run with the application metadata context.
    Where used:
        Alembic CLI online migration commands.
    Inputs:
        None; uses configured database_url from settings.
    Returns:
        None; executes migration scripts against the live DB.
    """
    settings = get_settings()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url
    # Build a SQLAlchemy engine with Alembic's configuration.
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Bind the connection and metadata to Alembic context.
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
