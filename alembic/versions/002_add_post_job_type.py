"""add post to job_type_enum

Revision ID: 002
Revises: 001
Create Date: 2026-02-25

"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_type_enum ADD VALUE IF NOT EXISTS 'post'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
