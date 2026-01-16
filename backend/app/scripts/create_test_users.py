"""Create QA users for multi-tenant concurrency tests."""

from __future__ import annotations

import os

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.models import RoleEnum, Tenant, User
from app.db.session import create_engine_from_settings, get_session_maker


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _ensure_tenant(db, slug: str, name: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
    if not tenant:
        tenant = Tenant(slug=slug, name=name)
        db.add(tenant)
        db.flush()
    return tenant


def _ensure_user(
    db,
    *,
    tenant_id: int,
    email: str,
    role: RoleEnum,
    hashed_password: str,
) -> bool:
    user = db.query(User).filter(User.tenant_id == tenant_id, User.email == email).first()
    if user:
        return False
    db.add(
        User(
            tenant_id=tenant_id,
            email=email,
            hashed_password=hashed_password,
            role=role,
        )
    )
    return True


def main() -> None:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    Session = get_session_maker(engine)

    password_default = _env("QA_PASSWORD", "password123")
    tenant_users = {
        "tenant_a": {
            "admin": ("QA_TENANT_A_ADMIN_EMAIL", "QA_TENANT_A_ADMIN_PASSWORD", "admin@alpha.example.com"),
            "user1": ("QA_TENANT_A_USER1_EMAIL", "QA_TENANT_A_USER1_PASSWORD", "user1@alpha.example.com"),
            "user2": ("QA_TENANT_A_USER2_EMAIL", "QA_TENANT_A_USER2_PASSWORD", "user2@alpha.example.com"),
        },
        "tenant_b": {
            "admin": ("QA_TENANT_B_ADMIN_EMAIL", "QA_TENANT_B_ADMIN_PASSWORD", "admin@beta.example.com"),
            "user1": ("QA_TENANT_B_USER1_EMAIL", "QA_TENANT_B_USER1_PASSWORD", "user1@beta.example.com"),
            "user2": ("QA_TENANT_B_USER2_EMAIL", "QA_TENANT_B_USER2_PASSWORD", "user2@beta.example.com"),
        },
        "tenant_c": {
            "admin": ("QA_TENANT_C_ADMIN_EMAIL", "QA_TENANT_C_ADMIN_PASSWORD", "admin@gamma.example.com"),
            "user1": ("QA_TENANT_C_USER1_EMAIL", "QA_TENANT_C_USER1_PASSWORD", "user1@gamma.example.com"),
            "user2": ("QA_TENANT_C_USER2_EMAIL", "QA_TENANT_C_USER2_PASSWORD", "user2@gamma.example.com"),
        },
    }
    tenants = {
        "tenant_a": ("alpha-store", "Alpha Store"),
        "tenant_b": ("beta-shop", "Beta Shop"),
        "tenant_c": ("gamma-mart", "Gamma Mart"),
    }

    created = 0
    with Session() as db:
        tenant_records = {
            key: _ensure_tenant(db, slug, name)
            for key, (slug, name) in tenants.items()
        }
        password_cache: dict[str, str] = {}
        for tenant_key, roles in tenant_users.items():
            tenant = tenant_records[tenant_key]
            for role_key, (email_env, password_env, fallback_email) in roles.items():
                email = _env(email_env, fallback_email)
                password = _env(password_env, password_default)
                hashed_password = password_cache.get(password)
                if hashed_password is None:
                    hashed_password = get_password_hash(password)
                    password_cache[password] = hashed_password
                role = RoleEnum.ADMIN if role_key == "admin" else RoleEnum.NORMAL
                if _ensure_user(
                    db,
                    tenant_id=tenant.id,
                    email=email,
                    role=role,
                    hashed_password=hashed_password,
                ):
                    created += 1
        db.commit()

    print(f"Created {created} test user(s).")


if __name__ == "__main__":
    main()
