"""Dependency helpers for FastAPI."""

from collections.abc import Iterator
from typing import Callable

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import oauth2_scheme
from app.db.clickhouse import get_clickhouse_client
from app.db.models import RoleEnum, User


@dataclass
class GuestUser:
    """Lightweight user representation for guest sessions."""
    id: int
    tenant_id: int
    email: str
    role: RoleEnum
    is_active: bool = True


def get_settings(request: Request) -> Settings:
    """Provide Settings from the FastAPI app state for dependency injection.

    Business purpose:
        Give handlers and services access to runtime configuration.
    Why it exists:
        FastAPI dependencies need a stable way to read initialized settings.
    Where used:
        Injected into API handlers, service layers, and auth helpers.
    Inputs:
        request: FastAPI Request carrying app.state.
    Returns:
        Settings object initialized at application startup.
    """
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session for relational reads/writes.

    Business purpose:
        Provide transactional access to the metadata store (users, tenants, reports).
    Why it exists:
        Centralizes session creation and cleanup per request.
    Where used:
        Injected into auth, tenant, and quality endpoints.
    Inputs:
        request: FastAPI Request with session maker on app.state.
    Returns:
        Iterator yielding a live SQLAlchemy Session, closed after request.
    """
    session_maker = request.app.state.session_maker
    db = session_maker()
    try:
        # Yield control to the request handler while keeping the session open.
        yield db
    finally:
        # Always close the session to avoid connection leaks.
        db.close()


def get_clickhouse(request: Request):
    """Provide a ClickHouse client for analytics queries with availability checks.

    Business purpose:
        Allow analytics endpoints to query ClickHouse safely.
    Why it exists:
        Centralizes client creation and enforces feature-flag availability.
    Where used:
        Injected into analytics and ETL endpoints that query ClickHouse.
    Inputs:
        request: FastAPI Request with settings on app.state.
    Returns:
        Generator yielding a ClickHouse client, disconnected after use.
    """
    settings = request.app.state.settings
    # Fail fast if analytics storage is disabled or unavailable.
    if not settings.clickhouse_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ClickHouse is not available",
        )
    # Create a short-lived client per request to avoid stale connections.
    client = get_clickhouse_client(settings)
    try:
        yield client
    finally:
        # Ensure the network connection is closed for each request.
        client.disconnect()


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | GuestUser:
    """Resolve the authenticated user from JWT/cookie for tenant scoping.

    Business purpose:
        Identify the caller so tenant isolation and role checks can be enforced.
    Why it exists:
        Centralizes authentication decoding and user lookup in one dependency.
    Where used:
        Injected into nearly all protected API handlers.
    Inputs:
        request: FastAPI Request used to read auth cookies if present.
        token: Bearer token from Authorization header (optional).
        db: SQLAlchemy session for user lookup.
        settings: Runtime security settings (secret, algorithm).
    Returns:
        User model representing the authenticated and active account.
    """
    # Standardized auth error returned for any credential validation failure.
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Prefer Authorization header, then fall back to session cookie.
    token = token or request.cookies.get("access_token")
    if not token:
        raise credentials_exception
    try:
        # Decode JWT to extract user identity for tenant scoping.
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        role = payload.get("role")
        tenant_id = payload.get("tenant_id")
        if user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    if role == RoleEnum.GUEST.value:
        if tenant_id is None:
            raise credentials_exception
        email = payload.get("email") or "guest@demo.local"
        return GuestUser(
            id=0,
            tenant_id=int(tenant_id),
            email=email,
            role=RoleEnum.GUEST,
        )

    # Look up the user and ensure the account is active.
    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(*roles: RoleEnum) -> Callable:
    """Build a dependency that enforces role-based access control.

    Business purpose:
        Limit sensitive endpoints (admin, ETL) to authorized roles only.
    Why it exists:
        Avoids duplicating role checks across handlers.
    Where used:
        Applied to admin-only or restricted API routes.
    Inputs:
        roles: One or more RoleEnum values permitted to access an endpoint.
    Returns:
        Dependency function that raises HTTP 403 if access is denied.
    """
    allowed = set(roles)

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        """Verify the current user role matches the allowed set.

        Business purpose:
            Protect privileged endpoints while preserving existing auth flow.
        Why it exists:
            Encapsulates role checks in a reusable dependency.
        Where used:
            Nested inside require_role; attached to FastAPI route definitions.
        Inputs:
            current_user: Authenticated user resolved by get_current_user.
        Returns:
            The same user if authorized; otherwise raises HTTP 403.
        """
        # Deny access when the user's role is not in the allowed set.
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _dependency
