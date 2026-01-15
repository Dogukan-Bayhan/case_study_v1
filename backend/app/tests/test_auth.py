"""Authentication endpoint tests covering login and identity lookup."""

from app.core.security import get_password_hash
from app.db.models import RoleEnum, Tenant, User


def test_login_and_me(client, db):
    """Verify login flow and identity endpoint.

    Business purpose:
        Ensure authentication issues tokens and exposes user identity.
    Why it exists:
        Protects against regressions in login and /auth/me behavior.
    Where used:
        Test suite for auth endpoints.
    Inputs:
        client: TestClient for HTTP requests.
        db: SQLAlchemy session for test data setup.
    Returns:
        None; asserts expected behavior.
    """
    # Create a tenant and admin user for login.
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

    # Login with valid credentials to obtain a token.
    resp = client.post(
        "/auth/login",
        data={"username": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    # Use the token to hit /auth/me and verify the returned identity.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"
