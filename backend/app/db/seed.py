"""Seed initial tenants and users if the database is empty."""

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.models import RoleEnum, Tenant, User
from app.db.session import create_engine_from_settings, get_session_maker


def seed_data() -> None:
    """Populate baseline tenants/users so the UI is usable on first run."""
    settings = get_settings()

    # Seeding is explicitly disabled by default in production environments.
    # This guard prevents accidental creation of demo data in real deployments.
    if not settings.seed_data:
        return

    engine = create_engine_from_settings(settings)
    SessionLocal = get_session_maker(engine)

    # If seed_data set to true, we initialize two tenats, alpha and beta
    # These tennets just creates for testing, for real-case we dont need to 
    # create tenants
    with SessionLocal() as db:
        # If the database already contains tenants, assume it has been initialized
        # and skip seeding to avoid creating duplicate demo records.
        if db.query(Tenant).count() > 0:
            return

        # Create two example tenants to demonstrate multi-tenant behavior.
        # These tenants are for development/demo purposes only.
        tenants = [
            Tenant(slug="alpha-store", name="Alpha Store"),
            Tenant(slug="beta-shop", name="Beta Shop"),
        ]
        db.add_all(tenants)

        # Flush is required to assign database-generated IDs to tenants
        # before creating users that reference them via foreign keys.
        db.flush()

        
        password = get_password_hash("password123")
        users = []
        for tenant in tenants:
                prefix = tenant.slug.split("-")[0]
                
                # Create one user per role to exercise role-based access control:
                # - Admin: full access
                # - Normal: tenant-scoped access
                # - Guest: read-only, restricted access
                users.extend(
                    [
                        User(
                            tenant_id=tenant.id,
                            email=f"admin@{prefix}.example.com",
                            hashed_password=password,
                            role=RoleEnum.ADMIN,
                        ),
                        User(
                            tenant_id=tenant.id,
                            email=f"user@{prefix}.example.com",
                            hashed_password=password,
                            role=RoleEnum.NORMAL,
                        ),
                        User(
                            tenant_id=tenant.id,
                            email=f"guest@{prefix}.example.com",
                            hashed_password=password,
                            role=RoleEnum.GUEST,
                        ),
                    ]
                )
        db.add_all(users)
        db.commit()


if __name__ == "__main__":
    seed_data()
