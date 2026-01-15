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
    """Create a user within the caller's tenant only."""
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if payload.tenant_slug and payload.tenant_slug != tenant.slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create users for other tenants",
        )
    user = create_user(db, payload.email, payload.password, tenant.id, payload.role)
    return UserOut(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> list[UserOut]:
    """List users for the current tenant for admin visibility."""
    users = db.query(User).filter(User.tenant_id == current_user.tenant_id).all()
    return [UserOut(id=u.id, tenant_id=u.tenant_id, email=u.email, role=u.role) for u in users]
