"""ETL API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import get_db, get_settings, require_role
from app.db.models import EtlRun, RoleEnum, Tenant, User
from app.etl.service import run_etl

router = APIRouter(prefix="/etl", tags=["etl"])


class EtlRequest(BaseModel):
    """Payload for triggering a tenant-scoped ETL run."""
    tenant_slug: str
    csv_path: str
    dry_run: bool = False


@router.post("/run")
def run_etl_endpoint(
    payload: EtlRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> dict:
    """Trigger an ETL run for the current tenant.

    Business purpose:
        Allow tenant admins to launch ETL ingestion for their data.
    Why it exists:
        Provides a protected API to run ETL with tenant isolation.
    Where used:
        Admin UI or API clients triggering ETL.
    Inputs:
        payload: EtlRequest with tenant slug, CSV path, and dry-run flag.
        db: SQLAlchemy session for tenant lookup and run tracking.
        settings: Runtime configuration for ETL execution.
        current_user: Admin user authorized to run ETL.
    Returns:
        Dict containing the run id and status.
    """
    # Resolve the tenant by slug and enforce tenant isolation.
    tenant = db.query(Tenant).filter(Tenant.slug == payload.tenant_slug).first()
    if tenant is None or tenant.id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    # Execute ETL for the tenant and return run metadata.
    run = run_etl(db, settings, tenant, payload.csv_path, current_user.id, dry_run=payload.dry_run)
    return {"run_id": run.id, "status": run.status}


@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> list[dict]:
    """List historical ETL runs for audit visibility.

    Business purpose:
        Provide an audit trail of ETL executions per tenant.
    Why it exists:
        Enables admins to inspect ETL status and errors.
    Where used:
        Admin UI and API clients.
    Inputs:
        db: SQLAlchemy session for query access.
        current_user: Admin user authorized to view tenant runs.
    Returns:
        List of dicts describing ETL runs for the tenant.
    """
    # Restrict listing to runs within the current tenant.
    runs = db.query(EtlRun).filter(EtlRun.tenant_id == current_user.tenant_id).all()
    return [
        {
            "id": run.id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
        }
        for run in runs
    ]
