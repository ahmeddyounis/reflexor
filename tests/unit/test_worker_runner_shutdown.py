from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, cast
from uuid import uuid4

import pytest

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.observability.context import request_id_context
from reflexor.observability.logging import build_json_handler
from reflexor.orchestrator.queue import Lease, QueueClosed, TaskEnvelope
from reflexor.worker.runner import WorkerRunner


class _NoopExecutor:
    async def process_lease(self, lease: Lease) -> None:  # pragma: no cover
        _ = lease
        raise AssertionError("executor should not be called when the queue is empty")


class _ExplodingExecutor:
    async def process_lease(self, lease: Lease) -> None:
        _ = lease
        raise RuntimeError("boom")


class _SecretExplodingExecutor:
    async def process_lease(self, lease: Lease) -> None:
        _ = lease
        raise RuntimeError("Bearer sk-worker-secret-should-not-leak")


class _RecordingQueue:
    def __init__(self) -> None:
        self.nack_calls: list[tuple[Lease, float | None, str | None]] = []

    async def enqueue(self, envelope: TaskEnvelope) -> None:  # pragma: no cover
        _ = envelope
        raise NotImplementedError

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:  # pragma: no cover
        _ = (timeout_s, wait_s)
        raise NotImplementedError

    async def ack(self, lease: Lease) -> None:  # pragma: no cover
        _ = lease
        raise NotImplementedError

    async def nack(
        self,
        lease: Lease,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        self.nack_calls.append((lease, delay_s, reason))

    async def aclose(self) -> None:  # pragma: no cover
        return


class _DequeueExplodingQueue(_RecordingQueue):
    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:
        _ = (timeout_s, wait_s)
        raise RuntimeError("Bearer sk-worker-dequeue-secret-should-not-leak")


def _lease() -> Lease:
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    return Lease(
        lease_id=str(uuid4()),
        envelope=envelope,
        leased_at_ms=0,
        visibility_timeout_s=30.0,
        attempt=0,
    )


async def test_worker_shutdown_unblocks_waiting_dequeue() -> None:
    queue = InMemoryQueue()
    stop_event = asyncio.Event()

    runner = WorkerRunner(
        queue=queue,
        executor=cast(Any, _NoopExecutor()),
        stop_event=stop_event,
        dequeue_wait_s=None,
        install_signal_handlers=False,
    )

    task = asyncio.create_task(runner.run())
    await asyncio.sleep(0)

    stop_event.set()
    await asyncio.wait_for(task, timeout=0.5)

    with pytest.raises(QueueClosed, match="queue is closed"):
        await queue.dequeue()


async def test_worker_runner_rejects_non_finite_timing_values() -> None:
    queue = InMemoryQueue()

    runner = WorkerRunner(
        queue=queue,
        executor=cast(Any, _NoopExecutor()),
        visibility_timeout_s=float("nan"),
        install_signal_handlers=False,
    )
    with pytest.raises(ValueError, match="visibility_timeout_s must be finite and > 0"):
        await runner.run()

    runner = WorkerRunner(
        queue=queue,
        executor=cast(Any, _NoopExecutor()),
        dequeue_wait_s=float("inf"),
        install_signal_handlers=False,
    )
    with pytest.raises(ValueError, match="dequeue_wait_s must be finite and >= 0 when provided"):
        await runner.run()


async def test_worker_exception_requeues_with_backoff() -> None:
    queue = _RecordingQueue()
    runner = WorkerRunner(
        queue=queue,
        executor=cast(Any, _ExplodingExecutor()),
        visibility_timeout_s=30.0,
        install_signal_handlers=False,
        close_queue_on_exit=False,
    )

    lease = _lease()
    await runner._handle_lease(lease)

    assert queue.nack_calls == [(lease, 1.0, "worker_exception")]


async def test_worker_logs_clear_stale_request_id_context() -> None:
    queue = _RecordingQueue()
    stream = io.StringIO()
    logger = logging.getLogger("reflexor.tests.worker_runner")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = [build_json_handler(stream=stream, settings=ReflexorSettings())]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        runner = WorkerRunner(
            queue=queue,
            executor=cast(Any, _ExplodingExecutor()),
            visibility_timeout_s=30.0,
            install_signal_handlers=False,
            close_queue_on_exit=False,
            logger=logger,
        )

        with request_id_context("req-stale"):
            await runner._handle_lease(_lease())
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    payload = json.loads(stream.getvalue().splitlines()[0])
    assert payload["message"] == "worker failed to process lease"
    assert payload["request_id"] is None


async def test_worker_process_error_logs_do_not_leak_raw_messages() -> None:
    queue = _RecordingQueue()
    stream = io.StringIO()
    logger = logging.getLogger("reflexor.tests.worker_runner.raw")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        runner = WorkerRunner(
            queue=queue,
            executor=cast(Any, _SecretExplodingExecutor()),
            visibility_timeout_s=30.0,
            install_signal_handlers=False,
            close_queue_on_exit=False,
            logger=logger,
        )
        await runner._handle_lease(_lease())
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    logged = stream.getvalue()
    assert "worker failed to process lease" in logged
    assert "sk-worker-secret-should-not-leak" not in logged


async def test_worker_dequeue_error_logs_do_not_leak_raw_messages() -> None:
    queue = _DequeueExplodingQueue()
    stream = io.StringIO()
    logger = logging.getLogger("reflexor.tests.worker_runner.dequeue")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        runner = WorkerRunner(
            queue=queue,
            executor=cast(Any, _NoopExecutor()),
            visibility_timeout_s=30.0,
            dequeue_wait_s=0.0,
            install_signal_handlers=False,
            close_queue_on_exit=False,
            logger=logger,
        )
        with pytest.raises(RuntimeError, match="sk-worker-dequeue-secret-should-not-leak"):
            await runner.run()
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    logged = stream.getvalue()
    assert "worker dequeue failed" in logged
    assert "sk-worker-dequeue-secret-should-not-leak" not in logged
