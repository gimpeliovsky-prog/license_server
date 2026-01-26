"""Make license key fingerprint unique

Revision ID: 0006_license_fp_uniq
Revises: 0005_ota_access
Create Date: 2026-01-26 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_license_fp_uniq"
down_revision = "0005_ota_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    fingerprint,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY fingerprint ORDER BY created_at ASC, id ASC) AS rn,
                    FIRST_VALUE(id) OVER (PARTITION BY fingerprint ORDER BY created_at ASC, id ASC) AS keep_id
                FROM license_keys
                WHERE fingerprint IS NOT NULL
            )
            UPDATE ota_access
            SET license_key_id = ranked.keep_id
            FROM ranked
            WHERE ota_access.license_key_id = ranked.id
              AND ranked.rn > 1;
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    fingerprint,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY fingerprint ORDER BY created_at ASC, id ASC) AS rn
                FROM license_keys
                WHERE fingerprint IS NOT NULL
            )
            DELETE FROM license_keys
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
            """
        )
    )
    op.create_index(
        "uq_license_keys_fingerprint",
        "license_keys",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_license_keys_fingerprint", table_name="license_keys")
