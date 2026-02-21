from __future__ import annotations

import json
import uuid

import pytest

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import DEFAULT_MAX_APPROVAL_PREVIEW_CHARS, Approval


def test_approval_pending_defaults_and_round_trip() -> None:
    approval = Approval(
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        tool_call_id=str(uuid.uuid4()),
        created_at_ms=0,
        payload_hash="  h1  ",
        preview="  hello  ",
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.decided_at_ms is None
    assert approval.decided_by is None
    assert approval.payload_hash == "h1"
    assert approval.preview == "hello"

    dumped = approval.model_dump()
    restored = Approval.model_validate(dumped)
    assert restored.model_dump() == dumped

    as_json = json.loads(approval.model_dump_json())
    assert as_json["status"] == "pending"


def test_approval_transitions_pending_to_approved() -> None:
    approval = Approval(
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        tool_call_id=str(uuid.uuid4()),
        created_at_ms=10,
    )

    decided = approval.approve(decided_by="  alice  ", decided_at_ms=11)
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_at_ms == 11
    assert decided.decided_by == "alice"

    with pytest.raises(ValueError, match="already been decided"):
        decided.deny(decided_by="bob", decided_at_ms=12)


def test_approval_transitions_pending_to_denied() -> None:
    approval = Approval(
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        tool_call_id=str(uuid.uuid4()),
        created_at_ms=10,
    )

    decided = approval.deny(decided_by=None, decided_at_ms=12)
    assert decided.status == ApprovalStatus.DENIED
    assert decided.decided_at_ms == 12
    assert decided.decided_by is None


def test_approval_requires_decided_at_when_decided() -> None:
    with pytest.raises(ValueError, match="decided_at_ms is required"):
        Approval(
            run_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            tool_call_id=str(uuid.uuid4()),
            status=ApprovalStatus.APPROVED,
            created_at_ms=0,
        )


def test_approval_rejects_decision_fields_when_pending() -> None:
    with pytest.raises(ValueError, match="decided_at_ms must be null"):
        Approval(
            run_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            tool_call_id=str(uuid.uuid4()),
            status=ApprovalStatus.PENDING,
            created_at_ms=0,
            decided_at_ms=1,
        )

    with pytest.raises(ValueError, match="decided_by must be null"):
        Approval(
            run_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            tool_call_id=str(uuid.uuid4()),
            status=ApprovalStatus.PENDING,
            created_at_ms=0,
            decided_by="alice",
        )


def test_approval_truncates_preview() -> None:
    approval = Approval(
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        tool_call_id=str(uuid.uuid4()),
        created_at_ms=0,
        preview="x" * (DEFAULT_MAX_APPROVAL_PREVIEW_CHARS + 10),
    )
    assert approval.preview is not None
    assert len(approval.preview) == DEFAULT_MAX_APPROVAL_PREVIEW_CHARS
