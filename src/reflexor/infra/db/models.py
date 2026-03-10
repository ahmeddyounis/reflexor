from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ux_events_source_dedupe_key", "source", "dedupe_key", unique=True),)

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    received_at_ms: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    started_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"

    tool_call_id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String)
    args: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)
    permission_scope: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    started_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String, nullable=True)


class TaskRow(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.run_id"), index=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("tool_calls.tool_call_id"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    timeout_s: Mapped[int] = mapped_column(Integer)
    depends_on: Mapped[list[str]] = mapped_column(JSON_VARIANT)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    started_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labels: Mapped[list[str]] = mapped_column(JSON_VARIANT)
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSON_VARIANT)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.run_id"))
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id"))
    tool_call_id: Mapped[str] = mapped_column(String, ForeignKey("tool_calls.tool_call_id"))
    status: Mapped[str] = mapped_column(String, index=True)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    decided_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    preview: Mapped[str | None] = mapped_column(String, nullable=True)


class RunPacketRow(Base):
    __tablename__ = "run_packets"

    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.run_id"), primary_key=True)
    packet_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    packet: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)


class EventSuppressionRow(Base):
    __tablename__ = "event_suppressions"

    signature_hash: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    event_source: Mapped[str] = mapped_column(String, index=True)
    signature: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)
    window_start_ms: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[int] = mapped_column(Integer)
    window_ms: Mapped[int] = mapped_column(Integer)
    suppressed_until_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resume_required: Mapped[bool] = mapped_column(Boolean)
    cleared_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cleared_by: Mapped[str | None] = mapped_column(String, nullable=True)
    cleared_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    updated_at_ms: Mapped[int] = mapped_column(Integer)
    expires_at_ms: Mapped[int] = mapped_column(Integer, index=True)


class IdempotencyLedgerRow(Base):
    __tablename__ = "idempotency_ledger"

    idempotency_key: Mapped[str] = mapped_column(String, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    result_json: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)
    created_at_ms: Mapped[int] = mapped_column(Integer)
    updated_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    expires_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class MemoryItemRow(Base):
    __tablename__ = "memory_items"

    memory_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id"), unique=True, index=True
    )
    event_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("events.event_id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_source: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    summary: Mapped[str] = mapped_column(String)
    content: Mapped[dict[str, object]] = mapped_column(JSON_VARIANT)
    tags: Mapped[list[str]] = mapped_column(JSON_VARIANT)
    created_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    updated_at_ms: Mapped[int] = mapped_column(Integer, index=True)


__all__ = [
    "ApprovalRow",
    "Base",
    "EventRow",
    "EventSuppressionRow",
    "IdempotencyLedgerRow",
    "MemoryItemRow",
    "RunPacketRow",
    "RunRow",
    "TaskRow",
    "ToolCallRow",
]
