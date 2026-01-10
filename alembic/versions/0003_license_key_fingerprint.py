"""license key fingerprint

Revision ID: 0003_license_key_fingerprint
Revises: 0002_erp_allowlist
Create Date: 2026-01-10 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_license_key_fingerprint"
down_revision = "0002_erp_allowlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("license_keys", sa.Column("fingerprint", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_license_keys_fingerprint"), "license_keys", ["fingerprint"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_license_keys_fingerprint"), table_name="license_keys")
    op.drop_column("license_keys", "fingerprint")
