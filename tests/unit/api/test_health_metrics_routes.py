from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from reflexor.api.routes.health import healthz
from reflexor.api.routes.metrics import metrics
from reflexor.observability.metrics import ReflexorMetrics


def _get_metric_value(text: str, name: str, *, metric: str | None = None) -> float | None:
    if metric is None:
        pattern = re.compile(rf"^{re.escape(name)}\s+([0-9eE+\-\.]+)$")
    else:
        pattern = re.compile(
            rf'^{re.escape(name)}\{{metric="{re.escape(metric)}"\}}\s+([0-9eE+\-\.]+)$'
        )
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return float(match.group(1))
    return None


@pytest.mark.asyncio
async def test_metrics_route_keeps_last_pending_value_and_counts_refresh_failures() -> None:
    metrics_registry = ReflexorMetrics.build()
    metrics_registry.approvals_pending_total.set(7)
    container = SimpleNamespace(
        count_pending_approvals=lambda *, timeout_s=1.0: _return_none(timeout_s),
        metrics=metrics_registry,
    )

    response = await metrics(cast(Any, container))

    assert response.headers["Cache-Control"] == "no-store"
    payload = bytes(response.body).decode()
    assert _get_metric_value(payload, "approvals_pending_total") == 7.0
    assert (
        _get_metric_value(
            payload,
            "metrics_refresh_failures_total",
            metric="approvals_pending_total",
        )
        == 1.0
    )


async def _return_none(timeout_s: float = 1.0) -> None:
    _ = timeout_s
    return None


@pytest.mark.asyncio
async def test_healthz_checks_db_and_queue_concurrently() -> None:
    async def ping_db(*, timeout_s: float = 1.0) -> bool:
        _ = timeout_s
        await asyncio.sleep(0.05)
        return True

    async def ping_queue(*, timeout_s: float = 0.2) -> bool:
        _ = timeout_s
        await asyncio.sleep(0.05)
        return True

    container = SimpleNamespace(
        ping_db=ping_db,
        ping_queue=ping_queue,
        settings=SimpleNamespace(profile="dev", queue_backend="inmemory"),
        orchestrator_engine=SimpleNamespace(clock=SimpleNamespace(now_ms=lambda: 123)),
    )

    started = time.perf_counter()
    response = await healthz(cast(Any, container))
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.09
