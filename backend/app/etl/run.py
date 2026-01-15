"""CLI entrypoint for ETL."""

import argparse

from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import create_engine_from_settings, get_session_maker
from app.etl.service import run_etl


def main() -> None:
    """CLI wrapper to launch ETL outside the API runtime."""
    parser = argparse.ArgumentParser(description="Run ETL for a tenant")
    parser.add_argument("--tenant", required=True, help="Tenant slug")
    parser.add_argument("--csv", required=True, help="CSV path in container")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate mapping and schema without inserting into ClickHouse",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    SessionLocal = get_session_maker(engine)

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == args.tenant).first()
        if tenant is None:
            raise SystemExit("Tenant not found")
        run_etl(db, settings, tenant, args.csv, None, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
