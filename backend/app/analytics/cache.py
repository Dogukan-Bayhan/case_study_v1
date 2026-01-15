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
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if not entry:
            return None
        if entry.expires_at <= time.time():
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: object) -> None:
        self._store[key] = CacheEntry(value=value, expires_at=time.time() + self._ttl)


def make_cache_key(
    prefix: str,
    tenant_id: int,
    owner_user_id: int | None,
    scope: str,
    filters: dict[str, object],
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "scope": scope,
        "filters": filters,
    }
    return f"{prefix}:{json.dumps(payload, sort_keys=True, default=str)}"
