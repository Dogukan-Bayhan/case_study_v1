"""Lightweight TTL cache for analytics responses."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass


@dataclass
class CacheEntry:
    value: object
    expires_at: float


class TTLCache:
    """Simple in-memory TTL cache with string keys."""

    def __init__(self, ttl_seconds: int = 20) -> None:
        """Initialize a small TTL cache for short-lived analytics results.

        Business purpose:
            Avoid repeated expensive analytics queries within a short time window.
        Why it exists:
            Provides a lightweight in-process cache without external dependencies.
        Where used:
            Analytics service layer for repeated dashboard reads.
        Inputs:
            ttl_seconds: Time-to-live in seconds for cached entries.
        Returns:
            None; initializes internal cache state.
        """
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> object | None:
        """Return a cached value if present and not expired.

        Business purpose:
            Serve cached analytics results when still valid.
        Why it exists:
            Reduces repeated query load on ClickHouse for frequent reads.
        Where used:
            Analytics service when caching KPI and breakdown results.
        Inputs:
            key: Cache key generated for a request.
        Returns:
            Cached value if valid, otherwise None.
        """
        entry = self._store.get(key)
        if not entry:
            return None
        # Expire entries based on TTL to prevent stale analytics.
        if entry.expires_at <= time.time():
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: object) -> None:
        """Store a value in the cache with a TTL-based expiration.

        Business purpose:
            Cache frequently requested analytics payloads.
        Why it exists:
            Keeps caching policy consistent in one place.
        Where used:
            Analytics service after fetching data from ClickHouse.
        Inputs:
            key: Cache key for the request.
            value: Response payload to store.
        Returns:
            None; updates the internal cache store.
        """
        self._store[key] = CacheEntry(value=value, expires_at=time.time() + self._ttl)


def make_cache_key(
    prefix: str,
    tenant_id: int,
    owner_user_id: int | None,
    scope: str,
    filters: dict[str, object],
) -> str:
    """Build a deterministic cache key for analytics responses.

    Business purpose:
        Ensure cache entries are scoped by tenant, user, scope, and filters.
    Why it exists:
        Prevents cache collisions across tenants and filter combinations.
    Where used:
        Analytics service when caching KPIs and breakdowns.
    Inputs:
        prefix: Identifier for the cached endpoint or query type.
        tenant_id: Tenant identifier for isolation.
        owner_user_id: Optional user id for per-user scoping.
        scope: Analytics scope ("clean", "issues", "all").
        filters: Filter map to include in the cache key.
    Returns:
        Stable string cache key for the request parameters.
    """
    # Serialize payload with sorted keys for a stable cache key.
    payload = {
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "scope": scope,
        "filters": filters,
    }
    return f"{prefix}:{json.dumps(payload, sort_keys=True, default=str)}"
