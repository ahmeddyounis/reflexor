from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.infra.db.models import Base
from reflexor.storage.ports import RunRecord


def _uuid() -> str:
    return str(uuid.uuid4())


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _assert_error_shape(payload: dict[str, object]) -> None:
    assert isinstance(payload.get("error_code"), str)
    assert isinstance(payload.get("message"), str)
    assert isinstance(payload.get("request_id"), str)


def test_validation_errors_return_400_and_structured_error(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_error_validation.db"
    _create_schema(db_path)

    app = create_app(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            enabled_scopes=[],
            database_url=f"sqlite+aiosqlite:///{db_path}",
        )
    )

    with TestClient(app) as client:
        response = client.post("/v1/events", json={})
        assert response.status_code == 400
        assert response.headers.get("X-Request-ID")
        payload = response.json()
        _assert_error_shape(payload)
        assert payload["error_code"] == "validation_error"
        assert isinstance(payload.get("details"), dict)


def test_not_found_returns_404_and_structured_error(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_error_not_found.db"
    _create_schema(db_path)

    app = create_app(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            enabled_scopes=[],
            database_url=f"sqlite+aiosqlite:///{db_path}",
        )
    )

    with TestClient(app) as client:
        missing = client.get(f"/v1/runs/{_uuid()}")
        assert missing.status_code == 404
        payload = missing.json()
        _assert_error_shape(payload)
        assert payload["error_code"] == "not_found"


def test_auth_errors_return_401_and_structured_error(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_error_auth.db"
    _create_schema(db_path)

    app = create_app(
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            enabled_scopes=[],
            database_url=f"sqlite+aiosqlite:///{db_path}",
            admin_api_key="secret",
        )
    )

    with TestClient(app) as client:
        response = client.get("/v1/runs")
        assert response.status_code == 401
        payload = response.json()
        _assert_error_shape(payload)
        assert payload["error_code"] == "unauthorized"


async def _seed_invalid_waiting_approval_state(container: AppContainer) -> str:
    run_id = _uuid()
    tool_call_id = _uuid()
    task_id = _uuid()
    approval_id = _uuid()

    tool_call = ToolCall(
        tool_call_id=tool_call_id,
        tool_name="tests.tool",
        args={"k": "v"},
        permission_scope="debug.echo",
        idempotency_key="k-1",
        status=ToolCallStatus.PENDING,
        created_at_ms=1,
        started_at_ms=2,  # invalid for pending tool_call once transitions are applied
    )
    task = Task(
        task_id=task_id,
        run_id=run_id,
        name="invalid-state",
        status=TaskStatus.WAITING_APPROVAL,
        tool_call=tool_call,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=1_000,
    )
    approval = Approval(
        approval_id=approval_id,
        run_id=run_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        status=ApprovalStatus.PENDING,
        created_at_ms=10,
        preview="deny me",
        payload_hash="hash",
    )

    uow = container.uow_factory()
    async with uow:
        run_repo = container.repos.run_repo(uow.session)
        task_repo = container.repos.task_repo(uow.session)
        approval_repo = container.repos.approval_repo(uow.session)

        await run_repo.create(
            RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=1,
                started_at_ms=None,
                completed_at_ms=None,
            )
        )
        await task_repo.create(task)
        await approval_repo.create(approval)

    return approval_id


def test_domain_invariant_errors_return_409_and_structured_error(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_error_domain.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )
    container = AppContainer.build(settings=settings)
    approval_id = asyncio.run(_seed_invalid_waiting_approval_state(container))

    app = create_app(container=container)
    with TestClient(app) as client:
        response = client.post(f"/v1/approvals/{approval_id}/deny", json={"decided_by": "tester"})
        assert response.status_code == 409
        payload = response.json()
        _assert_error_shape(payload)
        assert payload["error_code"] == "invariant_violation"
