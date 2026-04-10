"""Add buyer preferred language fields.

Revision ID: 0026_buyer_preferred_language
Revises: 0025_identity_review_cases_and_status
Create Date: 2026-04-10 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_buyer_preferred_language"
down_revision = "0025_identity_review_cases_and_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("buyer_channel_identities", sa.Column("preferred_language", sa.String(length=16), nullable=True))
    op.add_column("buyer_channel_identities", sa.Column("language_source", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("buyer_channel_identities", "language_source")
    op.drop_column("buyer_channel_identities", "preferred_language")
