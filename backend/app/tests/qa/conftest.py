"""Shared fixtures for black-box QA tests."""

from __future__ import annotations

import os

import httpx
import pytest
from clickhouse_driver import Client


def _env(name: str, default: str) -> str:
    """Read string environment overrides for QA runs."""
    value = os.getenv(name)
    return value if value is not None else default


def _env_int(name: str, default: int) -> int:
    """Parse integer environment overrides with explicit errors."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _env_float(name: str, default: float) -> float:
    """Parse float environment overrides with explicit errors."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a float.") from exc


@pytest.fixture(scope="session")
def api_base_url() -> str:
    """Base URL for QA HTTP requests."""
    return _env("ANALYTICS_API_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def api_timeout_seconds() -> float:
    """Default timeout for QA HTTP requests."""
    return _env_float("ANALYTICS_API_TIMEOUT", 15.0)


@pytest.fixture(scope="session")
def api_client(api_base_url: str, api_timeout_seconds: float) -> httpx.Client:
    """Shared HTTP client for QA tests."""
    client = httpx.Client(base_url=api_base_url, timeout=api_timeout_seconds, follow_redirects=True)
    yield client
    client.close()


@pytest.fixture(scope="session")
def qa_users() -> dict[str, dict[str, str]]:
    """Credential matrix used by QA tests across tenants."""
    password = _env("QA_PASSWORD", "password123")
    return {
        "alpha_admin": {
            "email": _env("QA_ALPHA_ADMIN_EMAIL", "admin@alpha.example.com"),
            "password": _env("QA_ALPHA_ADMIN_PASSWORD", password),
        },
        "alpha_user": {
            "email": _env("QA_ALPHA_USER_EMAIL", "user@alpha.example.com"),
            "password": _env("QA_ALPHA_USER_PASSWORD", password),
        },
        "beta_admin": {
            "email": _env("QA_BETA_ADMIN_EMAIL", "admin@beta.example.com"),
            "password": _env("QA_BETA_ADMIN_PASSWORD", password),
        },
        "beta_user": {
            "email": _env("QA_BETA_USER_EMAIL", "user@beta.example.com"),
            "password": _env("QA_BETA_USER_PASSWORD", password),
        },
    }


@pytest.fixture(scope="session")
def token_factory(api_client: httpx.Client, qa_users: dict[str, dict[str, str]]):
    """Return a helper that issues auth tokens for QA users."""
    def _issue(user_key: str) -> str:
        """Authenticate a QA user and return a bearer token for API calls."""
        creds = qa_users[user_key]
        response = api_client.post(
            "/auth/login",
            data={"username": creds["email"], "password": creds["password"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Login response missing access_token.")
        return token

    return _issue


@pytest.fixture(scope="session")
def clickhouse_host() -> str:
    """ClickHouse host for QA verification queries."""
    return _env("CLICKHOUSE_HOST", "localhost")


@pytest.fixture(scope="session")
def clickhouse_port() -> int:
    """ClickHouse port for QA verification queries."""
    return _env_int("CLICKHOUSE_PORT", 9000)


@pytest.fixture(scope="session")
def clickhouse_user() -> str:
    """ClickHouse user for QA verification queries."""
    return _env("CLICKHOUSE_USER", "default")


@pytest.fixture(scope="session")
def clickhouse_password() -> str:
    """ClickHouse password for QA verification queries."""
    return _env("CLICKHOUSE_PASSWORD", "")


@pytest.fixture(scope="session")
def clickhouse_database() -> str:
    """ClickHouse database name for QA verification queries."""
    return _env("CLICKHOUSE_DATABASE", "analytics")


@pytest.fixture(scope="session")
def clickhouse_client(
    clickhouse_host: str,
    clickhouse_port: int,
    clickhouse_user: str,
    clickhouse_password: str,
) -> Client:
    """Provide a ClickHouse client and fail fast if unreachable."""
    client = Client(
        host=clickhouse_host,
        port=clickhouse_port,
        user=clickhouse_user,
        password=clickhouse_password,
        database="default",
        settings={"use_numpy": False},
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - connection diagnostics
        raise RuntimeError(
            "ClickHouse is not reachable. Ensure docker compose is running and "
            "the CLICKHOUSE_* environment variables are correct."
        ) from exc
    return client


@pytest.fixture(scope="session")
def clickhouse_fact_table(clickhouse_database: str) -> str:
    """Return the fully qualified CLEAN fact table name."""
    return f"{clickhouse_database}.fact_transactions_clean"
