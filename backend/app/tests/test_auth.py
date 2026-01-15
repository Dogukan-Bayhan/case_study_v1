"""Authentication endpoint tests covering login and identity lookup."""

from app.core.security import get_password_hash
from app.db.models import RoleEnum, Tenant, User


def test_login_and_me(client, db):
    """Ensure valid credentials yield a token and /auth/me returns the user."""
    tenant = Tenant(slug="test-tenant", name="Test Tenant")
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email="admin@example.com",
        hashed_password=get_password_hash("password123"),
        role=RoleEnum.ADMIN,
    )
    db.add(user)
    db.commit()

    resp = client.post(
        "/auth/login",
        data={"username": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"
