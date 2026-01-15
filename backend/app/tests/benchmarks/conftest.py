"""Shared fixtures for benchmarks and multi-tenant tests."""

from __future__ import annotations

import os

import httpx
import pytest
from clickhouse_driver import Client

from .utils import (
    BENCHMARK_RUNS,
    BENCHMARK_SUMMARIES,
    CONCURRENCY_SUMMARIES,
    format_benchmark_run,
    format_benchmark_summary,
    format_concurrency_summary,
    QueryMetrics,
)

def _env(name: str, default: str) -> str:
    """Read string environment overrides for benchmark configuration.

    Business purpose:
        Allow benchmarks to be configured via environment variables.
    Why it exists:
        Benchmarks often run in CI or containers with env-based config.
    Where used:
        Benchmark fixtures in this module.
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
        Read numeric settings for benchmark configuration.
    Why it exists:
        Provides clear errors when env vars are misconfigured.
    Where used:
        Benchmark fixtures that require integer settings.
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
        Read float settings for benchmark configuration.
    Why it exists:
        Provides clear errors when env vars are misconfigured.
    Where used:
        Benchmark fixtures that require float settings.
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
    """Return the API base URL used by benchmark clients.

    Business purpose:
        Provide a consistent base URL for benchmark HTTP requests.
    Why it exists:
        Benchmarks need a configurable target endpoint.
    Where used:
        api_client fixture and benchmark tests.
    Inputs:
        None; reads from environment.
    Returns:
        Base URL string.
    """
    return _env("ANALYTICS_API_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def api_timeout_seconds() -> float:
    """Return the default timeout for benchmark HTTP requests.

    Business purpose:
        Avoid hanging tests by enforcing a timeout.
    Why it exists:
        Benchmarks should fail fast when the API is unresponsive.
    Where used:
        api_client fixture.
    Inputs:
        None; reads from environment.
    Returns:
        Timeout in seconds as a float.
    """
    return _env_float("ANALYTICS_API_TIMEOUT", 20.0)


@pytest.fixture(scope="session")
def api_client(api_base_url: str, api_timeout_seconds: float) -> httpx.Client:
    """Provide an HTTP client shared across benchmark tests.

    Business purpose:
        Reuse a configured client for benchmark HTTP requests.
    Why it exists:
        Avoids repeated client setup in each test.
    Where used:
        Benchmark tests that call API endpoints.
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
    """Return benchmark user credentials from environment defaults.

    Business purpose:
        Provide known credentials for benchmark authentication flows.
    Why it exists:
        Benchmarks need stable test users across environments.
    Where used:
        token_factory fixture and benchmark tests.
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
    """Return a helper that issues auth tokens for benchmark users.

    Business purpose:
        Simplify token retrieval for benchmark requests.
    Why it exists:
        Avoids repeating login flows in each benchmark.
    Where used:
        Benchmark tests that need Authorization headers.
    Inputs:
        api_client: Shared HTTP client for API calls.
        qa_users: Dict of credential sets.
    Returns:
        Callable that returns a bearer token for a user key.
    """
    def _issue(user_key: str) -> str:
        """Authenticate a benchmark user and return a bearer token.

        Business purpose:
            Obtain a token for subsequent benchmark requests.
        Why it exists:
            Encapsulates login flow and error handling.
        Where used:
            token_factory fixture in benchmark tests.
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
    """Return ClickHouse host for benchmark direct queries.

    Business purpose:
        Provide a configurable ClickHouse host for benchmarks.
    Why it exists:
        Benchmarks may run against different environments.
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
    """Return ClickHouse port for benchmark direct queries.

    Business purpose:
        Provide a configurable ClickHouse port for benchmarks.
    Why it exists:
        Benchmarks may run against different environments.
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
    """Return ClickHouse user for benchmark direct queries.

    Business purpose:
        Provide a configurable ClickHouse user for benchmarks.
    Why it exists:
        Benchmarks may run against different environments.
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
    """Return ClickHouse password for benchmark direct queries.

    Business purpose:
        Provide a configurable ClickHouse password for benchmarks.
    Why it exists:
        Benchmarks may run against different environments.
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
    """Return ClickHouse database name for benchmark queries.

    Business purpose:
        Provide a configurable ClickHouse database for benchmarks.
    Why it exists:
        Benchmarks may run against different schemas.
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
    """Provide a live ClickHouse client and fail fast if unreachable.

    Business purpose:
        Enable direct ClickHouse queries for benchmark validation.
    Why it exists:
        Benchmarks require a stable connection to ClickHouse.
    Where used:
        Benchmark tests that query ClickHouse directly.
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
        # Used to fail fast before running benchmark queries.
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
        Provide the clean fact table name for benchmark queries.
    Why it exists:
        Keeps table name formatting consistent in benchmarks.
    Where used:
        Benchmark tests that query the clean fact table.
    Inputs:
        clickhouse_database: Database name.
    Returns:
        Fully qualified table name string.
    """
    return f"{clickhouse_database}.fact_transactions_clean"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Emit benchmark summaries after pytest completes.

    Business purpose:
        Print aggregated benchmark results at the end of the test run.
    Why it exists:
        Provides a single summary location for benchmark outputs.
    Where used:
        Pytest terminal summary hook.
    Inputs:
        terminalreporter: Pytest reporter object.
        exitstatus: Pytest exit status code.
        config: Pytest config object.
    Returns:
        None; writes summary lines to the terminal.
    """
    if not BENCHMARK_RUNS and not BENCHMARK_SUMMARIES and not CONCURRENCY_SUMMARIES:
        return
    terminalreporter.section("Benchmark Results")
    for record in BENCHMARK_RUNS:
        metrics = QueryMetrics(
            query_id="recorded",
            elapsed_seconds=record["elapsed"],
            rows_processed=record["rows_processed"],
            read_rows=record["read_rows"],
            read_bytes=record["read_bytes"],
            result_rows=record["result_rows"],
            memory_usage_bytes=record["memory_bytes"],
            query_duration_ms=record["query_duration_ms"],
        )
        terminalreporter.write_line(
            format_benchmark_run(record["label"], record["run"], metrics)
        )
    for record in BENCHMARK_SUMMARIES:
        terminalreporter.write_line(
            format_benchmark_summary(
                record["label"],
                {
                    "min": record["min"],
                    "avg": record["avg"],
                    "max": record["max"],
                    "p95": record["p95"],
                },
            )
        )
    for record in CONCURRENCY_SUMMARIES:
        terminalreporter.write_line(format_concurrency_summary(record))
