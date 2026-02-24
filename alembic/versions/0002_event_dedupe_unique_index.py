"""Add unique event dedupe index.

Revision ID: 0002_event_dedupe_unique_index
Revises: 0001_initial_schema
Create Date: 2026-02-24
"""

from __future__ import annotations

from alembic import op

revision = "0002_event_dedupe_unique_index"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ux_events_source_dedupe_key",
        "events",
        ["source", "dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_events_source_dedupe_key", table_name="events")
