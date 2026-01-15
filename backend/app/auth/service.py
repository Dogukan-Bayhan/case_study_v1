"""Authentication services."""

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models import User


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Validate credentials and return the active user for the email."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


def issue_token(user: User, settings: Settings) -> str:
    """Create a signed access token scoped to the user's tenant."""
    return create_access_token({"sub": str(user.id), "tenant_id": user.tenant_id}, settings)


def create_user(db: Session, email: str, password: str, tenant_id: int, role: str) -> User:
    """Create a tenant-scoped user with a hashed password."""
    hashed = get_password_hash(password)
    user = User(email=email, hashed_password=hashed, tenant_id=tenant_id, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
