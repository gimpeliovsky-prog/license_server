"""add process jobs table

Revision ID: 0013_process_jobs
Revises: 0012_tenant_is_system
Create Date: 2026-03-14 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_process_jobs"
down_revision = "0012_tenant_is_system"
branch_labels = None
depends_on = None


process_job_status = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    name="processjobstatus",
    create_type=False,
)


def upgrade() -> None:
    process_job_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "process_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", process_job_status, nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("request_meta", sa.JSON(), nullable=True),
        sa.Column("result_meta", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_process_jobs_tenant_id"), "process_jobs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_process_jobs_job_type"), "process_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_process_jobs_status"), "process_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_process_jobs_correlation_id"), "process_jobs", ["correlation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_process_jobs_correlation_id"), table_name="process_jobs")
    op.drop_index(op.f("ix_process_jobs_status"), table_name="process_jobs")
    op.drop_index(op.f("ix_process_jobs_job_type"), table_name="process_jobs")
    op.drop_index(op.f("ix_process_jobs_tenant_id"), table_name="process_jobs")
    op.drop_table("process_jobs")
    process_job_status.drop(op.get_bind(), checkfirst=True)
