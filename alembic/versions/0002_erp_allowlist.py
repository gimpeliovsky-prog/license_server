"""erp allowlist

Revision ID: 0002_erp_allowlist
Revises: 0001_initial
Create Date: 2026-01-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_erp_allowlist"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_allowlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum("doctype", "method", name="erpallowlisttype"),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entry_type", "value", name="uq_erp_allowlist_entry"),
    )
    op.create_index(op.f("ix_erp_allowlist_entry_type"), "erp_allowlist", ["entry_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_erp_allowlist_entry_type"), table_name="erp_allowlist")
    op.drop_table("erp_allowlist")
    op.execute("DROP TYPE IF EXISTS erpallowlisttype")
