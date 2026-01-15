"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-12 12:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial metadata schema for tenants, users, and quality tracking.

    Business purpose:
        Establish core relational tables required by the application.
    Why it exists:
        Sets up tenants, users, ETL runs, and quality report storage.
    Where used:
        Alembic migration upgrade path.
    Inputs:
        None; uses Alembic operations context.
    Returns:
        None; applies schema changes.
    """
    # Tenants table defines the multi-tenant boundary.
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # Users table tracks authenticated accounts scoped to tenants.
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    # ETL runs table tracks ingestion executions for auditability.
    op.create_table(
        "etl_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_etl_runs_tenant_id", "etl_runs", ["tenant_id"], unique=False)

    # Quality reports summarize ETL validation results.
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("etl_run_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["etl_run_id"], ["etl_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_quality_reports_tenant_id", "quality_reports", ["tenant_id"], unique=False)
    op.create_index("ix_quality_reports_etl_run_id", "quality_reports", ["etl_run_id"], unique=False)

    # Quality findings store individual rule-level findings per report.
    op.create_table(
        "quality_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("column", sa.String(length=128), nullable=True),
        sa.Column("check", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["quality_reports.id"]),
    )
    op.create_index("ix_quality_findings_report_id", "quality_findings", ["report_id"], unique=False)


def downgrade() -> None:
    """Drop the initial metadata schema in reverse dependency order.

    Business purpose:
        Provide a clean rollback path for the initial schema.
    Why it exists:
        Supports database downgrades during development or rollback.
    Where used:
        Alembic migration downgrade path.
    Inputs:
        None; uses Alembic operations context.
    Returns:
        None; removes schema objects.
    """
    # Drop indexes and tables in dependency order to satisfy constraints.
    op.drop_index("ix_quality_findings_report_id", table_name="quality_findings")
    op.drop_table("quality_findings")
    op.drop_index("ix_quality_reports_etl_run_id", table_name="quality_reports")
    op.drop_index("ix_quality_reports_tenant_id", table_name="quality_reports")
    op.drop_table("quality_reports")
    op.drop_index("ix_etl_runs_tenant_id", table_name="etl_runs")
    op.drop_table("etl_runs")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
