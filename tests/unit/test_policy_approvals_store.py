from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval
from reflexor.security.policy.approvals import InMemoryApprovalStore


def _approval(*, tool_call_id: str, created_at_ms: int = 1_000) -> Approval:
    return Approval(
        run_id=str(uuid4()),
        task_id=str(uuid4()),
        tool_call_id=tool_call_id,
        created_at_ms=created_at_ms,
        status=ApprovalStatus.PENDING,
        preview="Preview",
    )


@pytest.mark.asyncio
async def test_create_get_and_get_by_tool_call() -> None:
    store = InMemoryApprovalStore()
    approval = _approval(tool_call_id=str(uuid4()))

    created = await store.create_pending(approval)
    fetched = await store.get(created.approval_id)
    fetched_by_tool_call = await store.get_by_tool_call(created.tool_call_id)

    assert fetched == created
    assert fetched_by_tool_call == created


@pytest.mark.asyncio
async def test_create_pending_is_idempotent_by_tool_call_id() -> None:
    store = InMemoryApprovalStore()
    tool_call_id = str(uuid4())
    first = await store.create_pending(_approval(tool_call_id=tool_call_id))

    second_request = _approval(tool_call_id=tool_call_id)
    assert second_request.approval_id != first.approval_id

    second = await store.create_pending(second_request)
    assert second.approval_id == first.approval_id

    pending = await store.list_pending(limit=10, offset=0)
    assert len(pending) == 1
    assert pending[0].approval_id == first.approval_id


@pytest.mark.asyncio
async def test_list_pending_paginates_by_created_at() -> None:
    store = InMemoryApprovalStore()
    first = await store.create_pending(_approval(tool_call_id=str(uuid4()), created_at_ms=1_000))
    second = await store.create_pending(_approval(tool_call_id=str(uuid4()), created_at_ms=2_000))

    page1 = await store.list_pending(limit=1, offset=0)
    page2 = await store.list_pending(limit=1, offset=1)

    assert [item.approval_id for item in page1] == [first.approval_id]
    assert [item.approval_id for item in page2] == [second.approval_id]


@pytest.mark.asyncio
async def test_decide_approves_and_removes_from_pending() -> None:
    store = InMemoryApprovalStore()
    created = await store.create_pending(_approval(tool_call_id=str(uuid4())))

    decided = await store.decide(created.approval_id, ApprovalStatus.APPROVED, decided_by="alice")
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_at_ms is not None
    assert decided.decided_by == "alice"

    pending = await store.list_pending(limit=10, offset=0)
    assert pending == []

    fetched = await store.get(created.approval_id)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_decide_is_idempotent_for_same_terminal_decision_and_rejects_conflicts() -> None:
    store = InMemoryApprovalStore()
    created = await store.create_pending(_approval(tool_call_id=str(uuid4())))

    approved = await store.decide(created.approval_id, ApprovalStatus.APPROVED, decided_by="alice")
    approved_again = await store.decide(
        created.approval_id,
        ApprovalStatus.APPROVED,
        decided_by="bob",
    )

    assert approved_again == approved

    with pytest.raises(ValueError, match="already been decided as approved"):
        await store.decide(created.approval_id, ApprovalStatus.DENIED, decided_by="carol")


@pytest.mark.asyncio
async def test_concurrent_create_pending_is_idempotent() -> None:
    store = InMemoryApprovalStore()
    tool_call_id = str(uuid4())

    async def create_one() -> str:
        created = await store.create_pending(_approval(tool_call_id=tool_call_id))
        return created.approval_id

    ids = await asyncio.gather(*[create_one() for _ in range(25)])
    assert len(set(ids)) == 1

    pending = await store.list_pending(limit=10, offset=0)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_create_pending_requires_pending_status() -> None:
    store = InMemoryApprovalStore()
    approval = _approval(tool_call_id=str(uuid4())).approve(decided_by="alice")

    with pytest.raises(ValueError, match="status=pending"):
        await store.create_pending(approval)


@pytest.mark.asyncio
async def test_list_pending_validates_pagination_args() -> None:
    store = InMemoryApprovalStore()

    with pytest.raises(ValueError, match="limit must be >= 0"):
        await store.list_pending(limit=-1, offset=0)
    with pytest.raises(ValueError, match="offset must be >= 0"):
        await store.list_pending(limit=1, offset=-1)

    assert await store.list_pending(limit=0, offset=0) == []


@pytest.mark.asyncio
async def test_decide_validates_decision_and_unknown_ids() -> None:
    store = InMemoryApprovalStore()
    created = await store.create_pending(_approval(tool_call_id=str(uuid4())))

    with pytest.raises(ValueError, match="decision must be approved or denied"):
        await store.decide(created.approval_id, ApprovalStatus.PENDING, decided_by="alice")
    with pytest.raises(ValueError, match="decision must be approved or denied"):
        await store.decide(created.approval_id, ApprovalStatus.CANCELED, decided_by="alice")

    with pytest.raises(KeyError, match="unknown approval_id"):
        await store.decide(str(uuid4()), ApprovalStatus.APPROVED, decided_by="alice")


@pytest.mark.asyncio
async def test_missing_gets_return_none() -> None:
    store = InMemoryApprovalStore()
    assert await store.get(str(uuid4())) is None
    assert await store.get_by_tool_call(str(uuid4())) is None
