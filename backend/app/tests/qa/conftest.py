"""Shared fixtures for black-box QA tests."""

from __future__ import annotations

import os

import httpx
import pytest
from clickhouse_driver import Client


def _env(name: str, default: str) -> str:
    """Read string environment overrides for QA runs.

    Business purpose:
        Allow QA tests to be configured via environment variables.
    Why it exists:
        QA runs often execute in CI or containers with env-based config.
    Where used:
        QA fixtures in this module.
    Inputs:
        name: Environment variable name.
        default: Fallback value when not set.
    Returns:
        Configured string value.
    """
    value = os.getenv(name)
    return value if value is not None else default


def _env_int(name: str, default: int) -> int:
    """Parse integer environment overrides with explicit errors.

    Business purpose:
        Read numeric QA settings from environment.
    Why it exists:
        Provides clear errors for misconfigured variables.
    Where used:
        QA fixtures that require integer settings.
    Inputs:
        name: Environment variable name.
        default: Fallback integer when not set.
    Returns:
        Parsed integer value.
    """
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _env_float(name: str, default: float) -> float:
    """Parse float environment overrides with explicit errors.

    Business purpose:
        Read float QA settings from environment.
    Why it exists:
        Provides clear errors for misconfigured variables.
    Where used:
        QA fixtures that require float settings.
    Inputs:
        name: Environment variable name.
        default: Fallback float when not set.
    Returns:
        Parsed float value.
    """
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a float.") from exc


@pytest.fixture(scope="session")
def api_base_url() -> str:
    """Return the base URL for QA HTTP requests.

    Business purpose:
        Provide a consistent base URL for QA API calls.
    Why it exists:
        QA tests run against different environments.
    Where used:
        api_client fixture and QA tests.
    Inputs:
        None; reads from environment.
    Returns:
        Base URL string.
    """
    return _env("ANALYTICS_API_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def api_timeout_seconds() -> float:
    """Return the default timeout for QA HTTP requests.

    Business purpose:
        Avoid hanging QA tests when the API is unresponsive.
    Why it exists:
        Enforces a consistent timeout across QA runs.
    Where used:
        api_client fixture.
    Inputs:
        None; reads from environment.
    Returns:
        Timeout in seconds as a float.
    """
    return _env_float("ANALYTICS_API_TIMEOUT", 15.0)


@pytest.fixture(scope="session")
def api_client(api_base_url: str, api_timeout_seconds: float) -> httpx.Client:
    """Provide a shared HTTP client for QA tests.

    Business purpose:
        Reuse a configured client for QA HTTP requests.
    Why it exists:
        Avoids repeated client setup in each test.
    Where used:
        QA tests that call API endpoints.
    Inputs:
        api_base_url: Base URL for the API.
        api_timeout_seconds: Request timeout in seconds.
    Returns:
        httpx.Client instance with configured base URL and timeout.
    """
    client = httpx.Client(base_url=api_base_url, timeout=api_timeout_seconds, follow_redirects=True)
    yield client
    client.close()


@pytest.fixture(scope="session")
def qa_users() -> dict[str, dict[str, str]]:
    """Return the credential matrix used by QA tests across tenants.

    Business purpose:
        Provide known test users for QA authentication flows.
    Why it exists:
        QA tests require stable credentials across environments.
    Where used:
        token_factory fixture and QA tests.
    Inputs:
        None; reads from environment variables.
    Returns:
        Dict mapping user keys to credential dicts.
    """
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
    """Return a helper that issues auth tokens for QA users.

    Business purpose:
        Simplify token retrieval for QA requests.
    Why it exists:
        Avoids repeating login flows in each test.
    Where used:
        QA tests requiring Authorization headers.
    Inputs:
        api_client: Shared HTTP client for API calls.
        qa_users: Dict of credential sets.
    Returns:
        Callable that returns a bearer token for a user key.
    """
    def _issue(user_key: str) -> str:
        """Authenticate a QA user and return a bearer token.

        Business purpose:
            Obtain a token for subsequent QA requests.
        Why it exists:
            Encapsulates login flow and error handling.
        Where used:
            token_factory fixture in QA tests.
        Inputs:
            user_key: Key identifying a user in qa_users.
        Returns:
            JWT access token string.
        """
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
    """Return ClickHouse host for QA verification queries.

    Business purpose:
        Provide a configurable ClickHouse host for QA runs.
    Why it exists:
        QA runs may target different environments.
    Where used:
        clickhouse_client fixture.
    Inputs:
        None; reads from environment variables.
    Returns:
        Host string.
    """
    return _env("CLICKHOUSE_HOST", "localhost")


@pytest.fixture(scope="session")
def clickhouse_port() -> int:
    """Return ClickHouse port for QA verification queries.

    Business purpose:
        Provide a configurable ClickHouse port for QA runs.
    Why it exists:
        QA runs may target different environments.
    Where used:
        clickhouse_client fixture.
    Inputs:
        None; reads from environment variables.
    Returns:
        Port number.
    """
    return _env_int("CLICKHOUSE_PORT", 9000)


@pytest.fixture(scope="session")
def clickhouse_user() -> str:
    """Return ClickHouse user for QA verification queries.

    Business purpose:
        Provide a configurable ClickHouse user for QA runs.
    Why it exists:
        QA runs may target different environments.
    Where used:
        clickhouse_client fixture.
    Inputs:
        None; reads from environment variables.
    Returns:
        Username string.
    """
    return _env("CLICKHOUSE_USER", "default")


@pytest.fixture(scope="session")
def clickhouse_password() -> str:
    """Return ClickHouse password for QA verification queries.

    Business purpose:
        Provide a configurable ClickHouse password for QA runs.
    Why it exists:
        QA runs may target different environments.
    Where used:
        clickhouse_client fixture.
    Inputs:
        None; reads from environment variables.
    Returns:
        Password string.
    """
    return _env("CLICKHOUSE_PASSWORD", "")


@pytest.fixture(scope="session")
def clickhouse_database() -> str:
    """Return ClickHouse database name for QA verification queries.

    Business purpose:
        Provide a configurable ClickHouse database for QA runs.
    Why it exists:
        QA runs may target different schemas.
    Where used:
        clickhouse_fact_table fixture.
    Inputs:
        None; reads from environment variables.
    Returns:
        Database name string.
    """
    return _env("CLICKHOUSE_DATABASE", "analytics")


@pytest.fixture(scope="session")
def clickhouse_client(
    clickhouse_host: str,
    clickhouse_port: int,
    clickhouse_user: str,
    clickhouse_password: str,
) -> Client:
    """Provide a ClickHouse client and fail fast if unreachable.

    Business purpose:
        Enable direct ClickHouse validation queries in QA tests.
    Why it exists:
        QA tests need a stable connection to ClickHouse.
    Where used:
        QA tests that cross-check ClickHouse data.
    Inputs:
        clickhouse_host: ClickHouse host.
        clickhouse_port: ClickHouse port.
        clickhouse_user: ClickHouse username.
        clickhouse_password: ClickHouse password.
    Returns:
        ClickHouse Client instance.
    """
    client = Client(
        host=clickhouse_host,
        port=clickhouse_port,
        user=clickhouse_user,
        password=clickhouse_password,
        database="default",
        settings={"use_numpy": False},
    )
    try:
        # Query is a minimal connectivity probe against ClickHouse.
        # SELECT 1 avoids touching analytics tables or scanning data parts.
        # Used to fail fast before running QA validation queries.
        client.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - connection diagnostics
        raise RuntimeError(
            "ClickHouse is not reachable. Ensure docker compose is running and "
            "the CLICKHOUSE_* environment variables are correct."
        ) from exc
    return client


@pytest.fixture(scope="session")
def clickhouse_fact_table(clickhouse_database: str) -> str:
    """Return the fully qualified CLEAN fact table name.

    Business purpose:
        Provide the clean fact table name for QA validation queries.
    Why it exists:
        Keeps table name formatting consistent in QA tests.
    Where used:
        QA tests that query the clean fact table.
    Inputs:
        clickhouse_database: Database name.
    Returns:
        Fully qualified table name string.
    """
    return f"{clickhouse_database}.fact_transactions_clean"
