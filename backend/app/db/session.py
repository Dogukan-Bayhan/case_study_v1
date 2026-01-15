"""Database session and engine helpers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_engine_from_settings(settings: Settings):
    """Create a SQLAlchemy engine tuned for the configured database backend."""
    url = make_url(settings.database_url)
    if url.drivername.startswith("sqlite"):
        return create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


def get_session_maker(engine) -> sessionmaker[Session]:
    """Return a session factory with predictable commit/expire behavior."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
