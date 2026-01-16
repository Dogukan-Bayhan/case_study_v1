"""Auth routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.schemas import GuestRequest, SignupRequest, Token, UserOut
from app.auth.service import authenticate_user, create_user, issue_guest_token, issue_token
from app.core.config import Settings
from app.core.deps import get_current_user, get_db, get_settings
from app.db.models import RoleEnum, Tenant, User

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_tenant(db: Session, tenant_slug: str | None) -> Tenant:
    """Resolve a tenant by slug or fall back to the first tenant."""
    query = db.query(Tenant)
    if tenant_slug:
        tenant = query.filter(Tenant.slug == tenant_slug).first()
        if tenant:
            return tenant
    tenant = query.order_by(Tenant.id.asc()).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tenant available")
    return tenant


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


@router.post("/signup", response_model=Token)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Token:
    """Register a normal user and return a JWT token.

    Business purpose:
        Allow new users to create accounts and access tenant data.
    Why it exists:
        Provides a sign-up flow for normal users.
    Where used:
        POST /auth/signup from web UI or API clients.
    Inputs:
        payload: SignupRequest with email, password, full name, and optional tenant_slug.
        db: SQLAlchemy session for user creation.
        settings: Security settings used to sign the token.
    Returns:
        Token response with access_token and token_type.
    """
    tenant = _resolve_tenant(db, payload.tenant_slug)
    existing = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.email == payload.email)
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    user = create_user(
        db,
        payload.email,
        payload.password,
        tenant.id,
        RoleEnum.NORMAL,
        full_name=payload.full_name,
    )
    token = issue_token(user, settings)
    return Token(access_token=token)


@router.post("/guest", response_model=Token)
def guest_access(
    payload: GuestRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Token:
    """Issue a temporary guest token for read-only access.

    Business purpose:
        Enable demo access without creating a persistent account.
    Why it exists:
        Guests should explore the platform without registration.
    Where used:
        POST /auth/guest from web UI or API clients.
    Inputs:
        payload: Optional GuestRequest with tenant_slug.
        db: SQLAlchemy session for tenant resolution.
        settings: Security settings used to sign the token.
    Returns:
        Token response with access_token and token_type.
    """
    tenant_slug = payload.tenant_slug if payload else None
    tenant = _resolve_tenant(db, tenant_slug)
    token, _ = issue_guest_token(tenant, settings)
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
        owner_user_id=current_user.id,
        full_name=getattr(current_user, "full_name", None),
    )
