"""Auth routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.schemas import Token, UserOut
from app.auth.service import authenticate_user, issue_token
from app.core.config import Settings
from app.core.deps import get_current_user, get_db, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Token:
    """Authenticate user credentials and return a JWT token.

    Business purpose:
        Provide login for API clients and the web UI.
    Why it exists:
        Centralizes credential exchange for JWT issuance.
    Where used:
        POST /auth/login from login form or API clients.
    Inputs:
        form_data: OAuth2 form with username/password.
        db: SQLAlchemy session for user lookup.
        settings: Security settings used to sign the token.
    Returns:
        Token response with access_token and token_type.
    """
    user = authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        # Avoid leaking which credential failed.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    # Issue a signed JWT for the authenticated user.
    token = issue_token(user, settings)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user=Depends(get_current_user)) -> UserOut:
    """Return the current authenticated user profile.

    Business purpose:
        Provide user context for UI personalization and client logic.
    Why it exists:
        Standard endpoint for validating sessions and retrieving user info.
    Where used:
        GET /auth/me by frontend session checks.
    Inputs:
        current_user: Authenticated user resolved by dependency.
    Returns:
        UserOut public profile for the authenticated user.
    """
    # Return only safe, public fields for the UI.
    return UserOut(
        id=current_user.id,
        tenant_id=current_user.tenant_id,
        email=current_user.email,
        role=current_user.role,
    )
