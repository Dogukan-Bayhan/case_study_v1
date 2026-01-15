"""SQLAlchemy ORM models."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base shared by all Postgres-backed metadata models."""
    pass


class RoleEnum(str, enum.Enum):
    """User roles that drive tenant-scoped access rules."""
    ADMIN = "admin"
    NORMAL = "normal"
    GUEST = "guest"


class Tenant(Base):
    """Represents an isolated customer space for multi-tenant analytics."""
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    users: Mapped[list[User]] = relationship(back_populates="tenant")
    etl_runs: Mapped[list[EtlRun]] = relationship(back_populates="tenant")


class User(Base):
    """Accounts that authenticate API and UI access within a tenant."""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum, native_enum=False))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class EtlRun(Base):
    """Tracks ETL executions for auditing and operational debugging."""
    __tablename__ = "etl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="etl_runs")
    reports: Mapped[list[QualityReport]] = relationship(back_populates="etl_run")


class QualityReport(Base):
    """Stores aggregated quality summaries produced during ETL."""
    __tablename__ = "quality_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    etl_run_id: Mapped[int] = mapped_column(ForeignKey("etl_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary_json: Mapped[dict] = mapped_column(JSON)

    etl_run: Mapped[EtlRun] = relationship(back_populates="reports")
    findings: Mapped[list[QualityFinding]] = relationship(back_populates="report")


class QualityFinding(Base):
    """Persists individual quality findings tied to a report."""
    __tablename__ = "quality_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("quality_reports.id"), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    check: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    examples: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    report: Mapped[QualityReport] = relationship(back_populates="findings")
