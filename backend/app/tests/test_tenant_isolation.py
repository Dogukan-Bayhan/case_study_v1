"""Tenant isolation tests for admin operations."""

from app.core.security import get_password_hash
from app.db.models import RoleEnum, Tenant, User


def test_admin_cannot_create_user_for_other_tenant(client, db):
    """Ensure admins cannot create users outside their tenant."""
    tenant_a = Tenant(slug="tenant-a", name="Tenant A")
    tenant_b = Tenant(slug="tenant-b", name="Tenant B")
    db.add_all([tenant_a, tenant_b])
    db.flush()

    admin = User(
        tenant_id=tenant_a.id,
        email="admin@a.example.com",
        hashed_password=get_password_hash("password123"),
        role=RoleEnum.ADMIN,
    )
    db.add(admin)
    db.commit()

    login = client.post(
        "/auth/login",
        data={"username": "admin@a.example.com", "password": "password123"},
    )
    token = login.json()["access_token"]

    resp = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "user@b.example.com",
            "password": "password123",
            "role": "normal",
            "tenant_slug": "tenant-b",
        },
    )
    assert resp.status_code == 403
