"""Concurrent multi-tenant and multi-user isolation verification."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import time
import math

import httpx


@dataclass(frozen=True)
class UserSpec:
    key: str
    email: str
    password: str


@dataclass(frozen=True)
class UserContext:
    key: str
    token: str
    tenant_id: int
    user_id: int
    role: str


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    path: str
    params: dict[str, object] | None = None


@dataclass(frozen=True)
class RequestResult:
    user_key: str
    endpoint: str
    status_code: int
    payload: dict[str, object]


ENDPOINTS = [
    EndpointSpec(name="kpis", path="/analytics/kpis", params={"scope": "clean"}),
    EndpointSpec(
        name="transactions",
        path="/analytics/transactions",
        params={"page": 1, "page_size": 1, "sort_by": "order_date", "sort_dir": "desc"},
    ),
    EndpointSpec(name="quality_overview", path="/quality/overview"),
]
REQUEST_REPEATS = 2


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _load_user_specs() -> list[UserSpec]:
    password_default = _env("QA_PASSWORD", "password123")
    tenant_map = {
        "tenant_a": {
            "admin": ("QA_TENANT_A_ADMIN_EMAIL", "QA_TENANT_A_ADMIN_PASSWORD", "admin@alpha.example.com"),
            "user1": ("QA_TENANT_A_USER1_EMAIL", "QA_TENANT_A_USER1_PASSWORD", "user1@alpha.example.com"),
            "user2": ("QA_TENANT_A_USER2_EMAIL", "QA_TENANT_A_USER2_PASSWORD", "user2@alpha.example.com"),
        },
        "tenant_b": {
            "admin": ("QA_TENANT_B_ADMIN_EMAIL", "QA_TENANT_B_ADMIN_PASSWORD", "admin@beta.example.com"),
            "user1": ("QA_TENANT_B_USER1_EMAIL", "QA_TENANT_B_USER1_PASSWORD", "user1@beta.example.com"),
            "user2": ("QA_TENANT_B_USER2_EMAIL", "QA_TENANT_B_USER2_PASSWORD", "user2@beta.example.com"),
        },
        "tenant_c": {
            "admin": ("QA_TENANT_C_ADMIN_EMAIL", "QA_TENANT_C_ADMIN_PASSWORD", "admin@gamma.example.com"),
            "user1": ("QA_TENANT_C_USER1_EMAIL", "QA_TENANT_C_USER1_PASSWORD", "user1@gamma.example.com"),
            "user2": ("QA_TENANT_C_USER2_EMAIL", "QA_TENANT_C_USER2_PASSWORD", "user2@gamma.example.com"),
        },
    }
    specs: list[UserSpec] = []
    for tenant_key, roles in tenant_map.items():
        for role_key, (email_env, password_env, fallback_email) in roles.items():
            email = _env(email_env, fallback_email)
            specs.append(
                UserSpec(
                    key=f"{tenant_key}_{role_key}",
                    email=email,
                    password=_env(password_env, password_default),
                )
            )
    return specs


def _login(api_client: httpx.Client, user: UserSpec) -> str:
    response = api_client.post(
        "/auth/login",
        data={"username": user.email, "password": user.password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, (
        f"Login failed for {user.key} ({user.email}): {response.status_code} {response.text}"
    )
    token = response.json().get("access_token")
    assert token, f"Login response missing access_token for {user.key}."
    return token


def _fetch(
    api_base_url: str,
    api_timeout_seconds: float,
    spec: EndpointSpec,
    user_key: str,
    token: str,
) -> RequestResult:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=api_base_url, timeout=api_timeout_seconds) as client:
        response = client.get(spec.path, params=spec.params, headers=headers)
    assert response.status_code == 200, (
        f"{spec.name} failed for {user_key}: {response.status_code} {response.text}"
    )
    return RequestResult(
        user_key=user_key,
        endpoint=spec.name,
        status_code=response.status_code,
        payload=response.json(),
    )


def _normalize_payload(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
    if endpoint == "quality_overview":
        normalized = dict(payload)
        normalized.pop("as_of", None)
        return normalized
    return payload


def _assert_payloads_equal(endpoint: str, expected: dict[str, object], actual: dict[str, object], user_key: str) -> None:
    if endpoint != "kpis":
        assert actual == expected, (
            f"{endpoint} returned inconsistent payloads for {user_key} under concurrency."
        )
        return

    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, float) or isinstance(actual_value, float):
            assert math.isclose(
                float(actual_value),
                float(expected_value),
                rel_tol=1e-9,
                abs_tol=1e-6,
            ), (
                f"{endpoint} field '{key}' mismatch for {user_key}: "
                f"{actual_value} vs {expected_value}."
            )
        else:
            assert actual_value == expected_value, (
                f"{endpoint} field '{key}' mismatch for {user_key}: "
                f"{actual_value} vs {expected_value}."
            )


def test_parallel_users_multi_tenant(
    api_client,
    api_base_url,
    api_timeout_seconds,
    clickhouse_client,
    clickhouse_fact_table,
):
    """Verify concurrent multi-tenant requests remain isolated and deterministic."""
    users = _load_user_specs()
    tokens = {user.key: _login(api_client, user) for user in users}

    contexts: dict[str, UserContext] = {}
    for user in users:
        headers = {"Authorization": f"Bearer {tokens[user.key]}"}
        response = api_client.get("/auth/me", headers=headers)
        assert response.status_code == 200, f"/auth/me failed for {user.key}"
        payload = response.json()
        contexts[user.key] = UserContext(
            key=user.key,
            token=tokens[user.key],
            tenant_id=int(payload["tenant_id"]),
            user_id=int(payload["id"]),
            role=payload["role"],
        )

    expected_totals: dict[str, int] = {}
    for context in contexts.values():
        if context.role == "normal":
            query = (
                f"SELECT count() FROM {clickhouse_fact_table} "
                "WHERE tenant_id = %(tenant_id)s AND owner_user_id = %(owner_user_id)s"
            )
            params = {"tenant_id": context.tenant_id, "owner_user_id": context.user_id}
        else:
            query = f"SELECT count() FROM {clickhouse_fact_table} WHERE tenant_id = %(tenant_id)s"
            params = {"tenant_id": context.tenant_id}
        expected_totals[context.key] = int(clickhouse_client.execute(query, params)[0][0])

    futures = []
    start = time.monotonic()
    max_workers = min(16, len(contexts) * len(ENDPOINTS) * REQUEST_REPEATS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for context in contexts.values():
            for spec in ENDPOINTS:
                for _ in range(REQUEST_REPEATS):
                    futures.append(
                        executor.submit(
                            _fetch,
                            api_base_url,
                            api_timeout_seconds,
                            spec,
                            context.key,
                            context.token,
                        )
                    )
        results = [future.result() for future in as_completed(futures)]
    elapsed = time.monotonic() - start
    max_seconds_env = os.getenv("QA_CONCURRENCY_MAX_SECONDS")
    if max_seconds_env:
        max_seconds = float(max_seconds_env)
        assert elapsed < max_seconds, (
            f"Concurrent requests exceeded {max_seconds:.2f}s (took {elapsed:.2f}s)."
        )

    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for result in results:
        assert result.status_code == 200, f"{result.endpoint} failed for {result.user_key}"
        grouped.setdefault((result.user_key, result.endpoint), []).append(result.payload)

    for (user_key, endpoint), payloads in grouped.items():
        normalized = [_normalize_payload(endpoint, payload) for payload in payloads]
        first = normalized[0]
        for payload in normalized[1:]:
            _assert_payloads_equal(endpoint, first, payload, user_key)

    for user_key, context in contexts.items():
        overview = grouped[(user_key, "quality_overview")][0]
        assert int(overview["tenant_id"]) == context.tenant_id, (
            f"Tenant mismatch for {user_key}: expected {context.tenant_id}, "
            f"got {overview['tenant_id']}."
        )

        transactions = grouped[(user_key, "transactions")][0]
        assert int(transactions["total"]) == expected_totals[user_key], (
            f"Transactions total mismatch for {user_key}: expected {expected_totals[user_key]}, "
            f"got {transactions['total']}."
        )

        kpis = grouped[(user_key, "kpis")][0]
        assert kpis["revenue"] >= 0, f"Negative revenue for {user_key}"
        assert kpis["orders"] >= 0, f"Negative orders for {user_key}"
        assert kpis["avg_order_value"] >= 0, f"Negative avg_order_value for {user_key}"
        assert kpis["unique_customers"] >= 0, f"Negative unique_customers for {user_key}"
