"""Add clear-audit fields to event_suppressions.

Revision ID: 0007_event_suppressions_clear_audit
Revises: 0006_event_suppressions_table
Create Date: 2026-03-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_event_suppressions_clear_audit"
down_revision = "0006_event_suppressions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_suppressions", sa.Column("cleared_at_ms", sa.Integer(), nullable=True))
    op.add_column("event_suppressions", sa.Column("cleared_by", sa.String(), nullable=True))
    op.add_column(
        "event_suppressions",
        sa.Column("cleared_request_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_suppressions", "cleared_request_id")
    op.drop_column("event_suppressions", "cleared_by")
    op.drop_column("event_suppressions", "cleared_at_ms")
