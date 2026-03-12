from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import IdempotencyLedgerRow
from reflexor.observability.audit_sanitize import sanitize_tool_output
from reflexor.storage.idempotency import CachedOutcome, LedgerStatus, OutcomeToCache
from reflexor.tools.sdk import ToolResult


class SqlAlchemyIdempotencyLedger:
    """SQLAlchemy-backed adapter for the executor IdempotencyLedger port."""

    def __init__(self, session: AsyncSession, *, settings: ReflexorSettings | None = None) -> None:
        self._session = session
        self._settings = settings

    async def get_success(self, key: str) -> CachedOutcome | None:
        normalized = key.strip()
        if not normalized:
            raise ValueError("key must be non-empty")

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is None:
            return None

        if row.status != LedgerStatus.SUCCEEDED.value:
            return None

        now_ms = int(time.time() * 1000)
        if row.expires_at_ms is not None and row.expires_at_ms <= now_ms:
            return None

        cached_result = ToolResult.model_validate(row.result_json)
        return CachedOutcome(
            idempotency_key=row.idempotency_key,
            tool_name=row.tool_name,
            status=LedgerStatus.SUCCEEDED,
            result=cached_result,
            created_at_ms=row.created_at_ms,
            updated_at_ms=row.updated_at_ms,
            expires_at_ms=row.expires_at_ms,
        )

    async def record_success(self, key: str, outcome: OutcomeToCache) -> None:
        if not outcome.result.ok:
            raise ValueError("record_success requires an ok ToolResult")
        await self._upsert(
            key=key,
            outcome=outcome,
            status=LedgerStatus.SUCCEEDED,
            allow_overwrite=True,
        )

    async def record_failure(self, key: str, outcome: OutcomeToCache, transient: bool) -> None:
        if outcome.result.ok:
            raise ValueError("record_failure requires ok=false ToolResult")

        status = LedgerStatus.FAILED_TRANSIENT if transient else LedgerStatus.FAILED_PERMANENT
        await self._upsert(
            key=key,
            outcome=outcome,
            status=status,
            allow_overwrite=False,
        )

    async def _upsert(
        self,
        *,
        key: str,
        outcome: OutcomeToCache,
        status: LedgerStatus,
        allow_overwrite: bool,
    ) -> None:
        normalized = key.strip()
        if not normalized:
            raise ValueError("key must be non-empty")

        now_ms = int(time.time() * 1000)

        result_payload = outcome.result.model_dump(mode="json")
        sanitized_result = sanitize_tool_output(result_payload, settings=self._settings)
        if not isinstance(sanitized_result, dict):
            raise ValueError("sanitized tool result must be a JSON object")

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is not None:
            if not self._should_write_existing(
                row=row,
                outcome=outcome,
                allow_overwrite=allow_overwrite,
                now_ms=now_ms,
            ):
                return
            row.tool_name = outcome.tool_name
            row.status = status.value
            row.result_json = sanitized_result
            row.updated_at_ms = now_ms
            row.expires_at_ms = outcome.expires_at_ms
            await self._session.flush()
            return

        integrity_error: IntegrityError | None = None
        async with self._session.begin_nested() as nested:
            self._session.add(
                IdempotencyLedgerRow(
                    idempotency_key=normalized,
                    tool_name=outcome.tool_name,
                    status=status.value,
                    result_json=sanitized_result,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                    expires_at_ms=outcome.expires_at_ms,
                )
            )
            try:
                await self._session.flush()
            except IntegrityError as exc:
                integrity_error = exc
                await nested.rollback()
            else:
                return

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is not None:
            if not self._should_write_existing(
                row=row,
                outcome=outcome,
                allow_overwrite=allow_overwrite,
                now_ms=now_ms,
            ):
                return
            row.tool_name = outcome.tool_name
            row.status = status.value
            row.result_json = sanitized_result
            row.updated_at_ms = now_ms
            row.expires_at_ms = outcome.expires_at_ms
            await self._session.flush()
            return

        if integrity_error is not None:  # pragma: no cover
            raise integrity_error
        raise RuntimeError("failed to record outcome in idempotency ledger")

    def _should_write_existing(
        self,
        *,
        row: IdempotencyLedgerRow,
        outcome: OutcomeToCache,
        allow_overwrite: bool,
        now_ms: int,
    ) -> bool:
        if row.tool_name != outcome.tool_name:
            if row.status == LedgerStatus.SUCCEEDED.value and not self._is_expired(row, now_ms):
                return False
            return True
        if not allow_overwrite and row.status == LedgerStatus.SUCCEEDED.value:
            return False
        return True

    @staticmethod
    def _is_expired(row: IdempotencyLedgerRow, now_ms: int) -> bool:
        expires_at_ms = row.expires_at_ms
        return expires_at_ms is not None and int(expires_at_ms) <= int(now_ms)
