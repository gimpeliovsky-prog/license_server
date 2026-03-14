"""add request_key to process jobs

Revision ID: 0014_process_job_request_key
Revises: 0013_process_jobs
Create Date: 2026-03-14 21:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_process_job_request_key"
down_revision = "0013_process_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("process_jobs", sa.Column("request_key", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_process_jobs_request_key"), "process_jobs", ["request_key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_process_jobs_request_key"), table_name="process_jobs")
    op.drop_column("process_jobs", "request_key")
