"""Add idempotency_ledger table.

Revision ID: 0004_idempotency_ledger_table
Revises: 0003_run_packets_packet_version
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

revision = "0004_idempotency_ledger_table"
down_revision = "0003_run_packets_packet_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_ledger",
        sa.Column("idempotency_key", sa.String(), primary_key=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_json", JSON_TYPE, nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_idempotency_ledger_tool_name", "idempotency_ledger", ["tool_name"])
    op.create_index("ix_idempotency_ledger_status", "idempotency_ledger", ["status"])
    op.create_index("ix_idempotency_ledger_updated_at_ms", "idempotency_ledger", ["updated_at_ms"])
    op.create_index("ix_idempotency_ledger_expires_at_ms", "idempotency_ledger", ["expires_at_ms"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_ledger_expires_at_ms", table_name="idempotency_ledger")
    op.drop_index("ix_idempotency_ledger_updated_at_ms", table_name="idempotency_ledger")
    op.drop_index("ix_idempotency_ledger_status", table_name="idempotency_ledger")
    op.drop_index("ix_idempotency_ledger_tool_name", table_name="idempotency_ledger")
    op.drop_table("idempotency_ledger")
