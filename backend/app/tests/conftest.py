"""Shared test fixtures for API and database tests."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import Base
from app.db.session import create_engine_from_settings, get_session_maker
from app.main import create_app


@pytest.fixture
def app():
    """Create an isolated FastAPI app for tests.

    Business purpose:
        Provide a test application with in-memory database state.
    Why it exists:
        Ensures tests run in isolation without touching production resources.
    Where used:
        Pytest fixtures for API and service tests.
    Inputs:
        None; constructs Settings for a test environment.
    Returns:
        Configured FastAPI app with in-memory database bindings.
    """
    # Use in-memory SQLite and disable ClickHouse for fast tests.
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        clickhouse_enabled=False,
        secret_key="test",
        env="test",
        seed_data=False,
    )
    app = create_app(settings=settings)
    engine = create_engine_from_settings(settings)
    # Create all ORM tables in the in-memory database.
    Base.metadata.create_all(engine)
    app.state.db_engine = engine
    app.state.session_maker = get_session_maker(engine)
    return app


@pytest.fixture
def client(app):
    """Return a test client for request-level tests.

    Business purpose:
        Provide an HTTP client for exercising API routes in tests.
    Why it exists:
        Simplifies request-level tests by reusing a shared fixture.
    Where used:
        API tests across the backend suite.
    Inputs:
        app: Test FastAPI application.
    Returns:
        TestClient bound to the app.
    """
    return TestClient(app)


@pytest.fixture
def db(app):
    """Yield a database session bound to the in-memory engine.

    Business purpose:
        Provide a transactional session for database-related tests.
    Why it exists:
        Ensures tests have access to an isolated DB session.
    Where used:
        Tests that query or mutate the relational database.
    Inputs:
        app: Test FastAPI application.
    Returns:
        Generator yielding a SQLAlchemy session.
    """
    SessionLocal = app.state.session_maker
    with SessionLocal() as session:
        yield session
