"""add erp users table

Revision ID: 0009_erp_users
Revises: 0008_erp_idempotency
Create Date: 2026-03-04 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_erp_users"
down_revision = "0008_erp_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("erp_username", sa.String(length=140), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("erp_roles", sa.JSON(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_erp_users_tenant_id_tenants"),
        sa.UniqueConstraint("tenant_id", "erp_username", name="uq_erp_user_tenant_username"),
    )
    op.create_index(op.f("ix_erp_users_tenant_id"), "erp_users", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_erp_users_tenant_id"), table_name="erp_users")
    op.drop_table("erp_users")
