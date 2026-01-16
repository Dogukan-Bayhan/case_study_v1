"""Add full_name to users

Revision ID: 0002_add_user_full_name
Revises: 0001_initial
Create Date: 2026-01-16 12:10:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_add_user_full_name"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an optional full_name column to users for sign-up metadata."""
    op.add_column("users", sa.Column("full_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Drop full_name from users to revert schema changes."""
    op.drop_column("users", "full_name")
