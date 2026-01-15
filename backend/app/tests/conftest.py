"""Shared test fixtures for API and database tests."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import Base
from app.db.session import create_engine_from_settings, get_session_maker
from app.main import create_app


@pytest.fixture
def app():
    """Create an isolated FastAPI app backed by an in-memory database."""
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        clickhouse_enabled=False,
        secret_key="test",
        env="test",
        seed_data=False,
    )
    app = create_app(settings=settings)
    engine = create_engine_from_settings(settings)
    Base.metadata.create_all(engine)
    app.state.db_engine = engine
    app.state.session_maker = get_session_maker(engine)
    return app


@pytest.fixture
def client(app):
    """Return a test client for request-level tests."""
    return TestClient(app)


@pytest.fixture
def db(app):
    """Yield a database session bound to the in-memory engine."""
    SessionLocal = app.state.session_maker
    with SessionLocal() as session:
        yield session
