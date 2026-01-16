"""Authentication services."""

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models import RoleEnum, Tenant, User


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Authenticate a user by email and password.

    Business purpose:
        Validate credentials for login and return the active user.
    Why it exists:
        Centralizes credential verification and user state checks.
    Where used:
        Auth router during login.
    Inputs:
        db: SQLAlchemy session for user lookup.
        email: User email address.
        password: Raw password submitted by the user.
    Returns:
        User if authentication succeeds, otherwise None.
    """
    # Lookup is by email; tenant scoping is implicit in the user record.
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return None
    # Verify the password hash using the configured hashing policy.
    if not verify_password(password, user.hashed_password):
        return None
    # Reject inactive accounts to prevent access.
    if not user.is_active:
        return None
    return user


def issue_token(user: User, settings: Settings) -> str:
    """Issue a signed JWT for an authenticated user.

    Business purpose:
        Provide an access token for subsequent API requests.
    Why it exists:
        Encapsulates token creation and claim selection.
    Where used:
        Auth router after successful login.
    Inputs:
        user: Authenticated user record.
        settings: Security settings used to sign the token.
    Returns:
        Encoded JWT string.
    """
    # Embed user id and tenant id for downstream authorization checks.
    return create_access_token({"sub": str(user.id), "tenant_id": user.tenant_id}, settings)


def issue_guest_token(tenant: Tenant, settings: Settings) -> tuple[str, str]:
    """Issue a signed JWT for a temporary guest session.

    Business purpose:
        Provide read-only guest access without creating a persistent user.
    Why it exists:
        Guests should explore data without account creation.
    Where used:
        Guest login flows in API and web UI.
    Inputs:
        tenant: Tenant used to scope guest access.
        settings: Security settings used to sign the token.
    Returns:
        Tuple of (token, guest email) for UI display and /me response.
    """
    guest_email = f"guest@{tenant.slug}.example.com"
    payload = {
        "sub": "guest",
        "tenant_id": tenant.id,
        "role": RoleEnum.GUEST.value,
        "email": guest_email,
    }
    return create_access_token(payload, settings), guest_email


def create_user(
    db: Session,
    email: str,
    password: str,
    tenant_id: int,
    role: str,
    full_name: str | None = None,
) -> User:
    """Create a new tenant-scoped user with a hashed password.

    Business purpose:
        Provision users for a tenant with role-based access.
    Why it exists:
        Centralizes user creation and password hashing.
    Where used:
        Admin user creation endpoints and seed data.
    Inputs:
        db: SQLAlchemy session for persistence.
        email: New user email address.
        password: Raw password to hash.
        tenant_id: Tenant id to associate the user with.
        role: Role string for authorization.
    Returns:
        Persisted User model.
    """
    # Hash the password before storing to avoid plaintext persistence.
    hashed = get_password_hash(password)
    user = User(
        email=email,
        full_name=full_name,
        hashed_password=hashed,
        tenant_id=tenant_id,
        role=role,
    )
    # Persist the user and refresh to populate generated fields.
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
