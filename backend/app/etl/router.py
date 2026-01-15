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
    """Trigger an ETL run for the current tenant only."""
    tenant = db.query(Tenant).filter(Tenant.slug == payload.tenant_slug).first()
    if tenant is None or tenant.id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    run = run_etl(db, settings, tenant, payload.csv_path, current_user.id, dry_run=payload.dry_run)
    return {"run_id": run.id, "status": run.status}


@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleEnum.ADMIN)),
) -> list[dict]:
    """List historical ETL runs for audit visibility."""
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
