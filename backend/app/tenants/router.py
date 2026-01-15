"""Admin tenant management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.schemas import UserCreate, UserOut
from app.auth.service import create_user
from app.core.deps import get_db, require_role
from app.db.models import RoleEnum, Tenant, User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/users", response_model=UserOut)
def create_user_admin(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> UserOut:
    """Create a new user within the caller's tenant.

    Business purpose:
        Allow tenant admins to provision users in their organization.
    Why it exists:
        Enforces tenant isolation when creating accounts.
    Where used:
        Admin user management UI and API clients.
    Inputs:
        payload: UserCreate data including email, password, role, and optional tenant_slug.
        db: SQLAlchemy session for persistence.
        current_user: Admin user authorized to manage the tenant.
    Returns:
        UserOut for the newly created account.
    """
    # Load the tenant tied to the current admin for isolation enforcement.
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    # Prevent cross-tenant user creation even if tenant_slug is provided.
    if payload.tenant_slug and payload.tenant_slug != tenant.slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create users for other tenants",
        )
    # Create the user within the current tenant scope.
    user = create_user(db, payload.email, payload.password, tenant.id, payload.role)
    return UserOut(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> list[UserOut]:
    """List all users in the current tenant.

    Business purpose:
        Provide admins visibility into users in their tenant.
    Why it exists:
        Ensures user lists are scoped to the tenant context.
    Where used:
        Admin user management UI.
    Inputs:
        db: SQLAlchemy session for database access.
        current_user: Admin user authorized to view tenant users.
    Returns:
        List of UserOut entries for the tenant.
    """
    # Restrict listing to the current tenant only.
    users = db.query(User).filter(User.tenant_id == current_user.tenant_id).all()
    return [UserOut(id=u.id, tenant_id=u.tenant_id, email=u.email, role=u.role) for u in users]
