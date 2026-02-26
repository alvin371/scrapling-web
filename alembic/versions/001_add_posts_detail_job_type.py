"""add posts_detail to job_type_enum

Revision ID: 001
Revises:
Create Date: 2026-02-25

"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_type_enum ADD VALUE IF NOT EXISTS 'posts_detail'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # To downgrade, you would need to recreate the enum without 'posts_detail'.
    pass
