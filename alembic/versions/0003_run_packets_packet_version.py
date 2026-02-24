"""Add run_packets packet_version column.

Revision ID: 0003_run_packets_packet_version
Revises: 0002_event_dedupe_unique_index
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_packets_packet_version"
down_revision = "0002_event_dedupe_unique_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_packets",
        sa.Column(
            "packet_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_packets", "packet_version")
