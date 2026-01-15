"""Database session and engine helpers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_engine_from_settings(settings: Settings):
    """Create a SQLAlchemy engine for the configured relational database.

    Business purpose:
        Provide a shared database engine for ORM access.
    Why it exists:
        Centralizes engine creation and backend-specific tuning.
    Where used:
        App startup and tests that need a database engine.
    Inputs:
        settings: Runtime configuration containing database_url.
    Returns:
        SQLAlchemy Engine instance configured for the selected backend.
    """
    url = make_url(settings.database_url)
    # SQLite requires a StaticPool and check_same_thread for async-friendly tests.
    if url.drivername.startswith("sqlite"):
        return create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    # Production databases use pooled connections with health checks.
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


def get_session_maker(engine) -> sessionmaker[Session]:
    """Build a sessionmaker for consistent ORM sessions.

    Business purpose:
        Provide request-scoped SQLAlchemy sessions with predictable lifetimes.
    Why it exists:
        Centralizes session factory configuration.
    Where used:
        App startup and dependency injection in API handlers.
    Inputs:
        engine: SQLAlchemy Engine to bind sessions to.
    Returns:
        Configured sessionmaker that does not expire on commit.
    """
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
