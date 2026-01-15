"""Authentication and JWT helpers."""

from datetime import datetime, timedelta
from typing import Any

from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from passlib.context import CryptContext

from app.core.config import Settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Token extraction is optional because browser sessions also use cookies.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Validate a plaintext password against its stored hash.

    Business purpose:
        Authenticate users during login by comparing credentials safely.
    Why it exists:
        Centralizes password verification using the configured hashing policy.
    Where used:
        Auth service during login and credential checks.
    Inputs:
        plain_password: User-submitted password.
        hashed_password: Stored hash from the user record.
    Returns:
        True if the password matches, otherwise False.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Create a secure hash for a raw password.

    Business purpose:
        Store passwords safely while supporting verification on login.
    Why it exists:
        Ensures all password storage uses the same hashing algorithm.
    Where used:
        User creation, password updates, and seed data creation.
    Inputs:
        password: Raw password to hash.
    Returns:
        Hashed password string suitable for persistence.
    """
    return pwd_context.hash(password)


def create_access_token(
    data: dict[str, Any],
    settings: Settings,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT for API and web session authentication.

    Business purpose:
        Provide authenticated access tokens for the UI and API clients.
    Why it exists:
        Encapsulates JWT creation and expiration policy in one place.
    Where used:
        Auth service when issuing tokens after successful login.
    Inputs:
        data: Claims to embed in the token (e.g., user id).
        settings: Security settings (secret, algorithm, default TTL).
        expires_delta: Optional override for token lifetime.
    Returns:
        Encoded JWT string.
    """
    # Copy the payload to avoid mutating caller state.
    to_encode = data.copy()
    # Apply default expiration if the caller does not supply one.
    expire = datetime.utcnow() + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    # Include expiration so downstream auth can enforce session TTL.
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
