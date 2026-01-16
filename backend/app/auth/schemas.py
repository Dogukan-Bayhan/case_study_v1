"""Pydantic schemas for auth."""

from pydantic import BaseModel, EmailStr, Field

from app.db.models import RoleEnum


class Token(BaseModel):
    """JWT token payload returned after successful authentication."""
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """Public user shape exposed by API responses."""
    id: int
    tenant_id: int
    email: EmailStr
    role: RoleEnum
    owner_user_id: int | None = None
    full_name: str | None = None


class UserCreate(BaseModel):
    """Input payload for admin user creation."""
    email: EmailStr
    password: str = Field(min_length=8)
    role: RoleEnum
    tenant_slug: str | None = None


class SignupRequest(BaseModel):
    """Input payload for normal-user sign up."""
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    tenant_slug: str | None = None


class GuestRequest(BaseModel):
    """Input payload for guest access."""
    tenant_slug: str | None = None
