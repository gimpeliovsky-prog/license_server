"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2025-12-28 23:34:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_code", sa.String(length=64), nullable=False),
        sa.Column("erpnext_url", sa.String(length=255), nullable=False),
        sa.Column("api_key", sa.String(length=255), nullable=False),
        sa.Column("api_secret", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", "disabled", name="tenantstatus"),
            nullable=False,
        ),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_tenants_company_code"), "tenants", ["company_code"], unique=True)

    op.create_table(
        "license_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hashed_key", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "revoked", name="licensekeystatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_license_keys_tenant_id_tenants"),
    )
    op.create_index(op.f("ix_license_keys_tenant_id"), "license_keys", ["tenant_id"], unique=False)
    op.create_index(op.f("uq_license_keys_hashed_key"), "license_keys", ["hashed_key"], unique=True)

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_devices_tenant_id_tenants"),
        sa.UniqueConstraint("tenant_id", "device_id", name="uq_device_tenant"),
    )
    op.create_index(op.f("ix_devices_tenant_id"), "devices", ["tenant_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_logs_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], name="fk_audit_logs_device_id_devices"),
    )
    op.create_index(op.f("ix_audit_logs_tenant_id"), "audit_logs", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_tenant_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_devices_tenant_id"), table_name="devices")
    op.drop_table("devices")

    op.drop_index(op.f("uq_license_keys_hashed_key"), table_name="license_keys")
    op.drop_index(op.f("ix_license_keys_tenant_id"), table_name="license_keys")
    op.drop_table("license_keys")

    op.drop_index(op.f("ix_tenants_company_code"), table_name="tenants")
    op.drop_table("tenants")

    op.execute("DROP TYPE IF EXISTS tenantstatus")
    op.execute("DROP TYPE IF EXISTS licensekeystatus")
