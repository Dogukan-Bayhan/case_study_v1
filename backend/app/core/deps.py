"""Dependency helpers for FastAPI."""

from collections.abc import Iterator
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import oauth2_scheme
from app.db.clickhouse import get_clickhouse_client
from app.db.models import RoleEnum, User


def get_settings(request: Request) -> Settings:
    """Expose app settings stored on the FastAPI instance."""
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session and ensure cleanup."""
    session_maker = request.app.state.session_maker
    db = session_maker()
    try:
        yield db
    finally:
        db.close()


def get_clickhouse(request: Request):
    """Provide a ClickHouse client while guarding against disabled analytics."""
    settings = request.app.state.settings
    if not settings.clickhouse_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ClickHouse is not available",
        )
    client = get_clickhouse_client(settings)
    try:
        yield client
    finally:
        client.disconnect()


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the authenticated user from JWT or cookie for tenant scoping."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = token or request.cookies.get("access_token")
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(*roles: RoleEnum) -> Callable:
    """Gate endpoints by role without duplicating authorization logic."""
    allowed = set(roles)

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        """Enforce role checks while preserving the existing user resolution path."""
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _dependency
