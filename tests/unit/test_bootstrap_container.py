from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from reflexor.bootstrap import container as bootstrap_container
from reflexor.bootstrap.container import AppContainer, _AppPolicy, _AppResources
from reflexor.config import ReflexorSettings
from reflexor.observability.metrics import ReflexorMetrics


class _AsyncCloser:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.error is not None:
            raise self.error


class _AsyncDisposable:
    def __init__(self) -> None:
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


def _build_container(
    *,
    tmp_path: Path,
    orchestrator_error: Exception | None = None,
    circuit_breaker_error: Exception | None = None,
) -> tuple[AppContainer, _AsyncCloser, _AsyncCloser, _AsyncCloser, _AsyncDisposable]:
    orchestrator = _AsyncCloser(error=orchestrator_error)
    circuit_breaker = _AsyncCloser(error=circuit_breaker_error)
    queue = _AsyncCloser()
    engine = _AsyncDisposable()

    container = AppContainer(
        settings=ReflexorSettings(workspace_root=tmp_path),
        metrics=ReflexorMetrics.build(),
        resources=_AppResources(
            engine=cast(Any, engine),
            session_factory=cast(Any, object()),
            uow_factory=cast(Any, lambda: object()),
            queue=cast(Any, queue),
            owns_engine=True,
            owns_queue=True,
        ),
        repos=cast(Any, object()),
        policy=_AppPolicy(
            tool_registry=cast(Any, object()),
            tool_runner=cast(Any, object()),
            policy_gate=cast(Any, object()),
            policy_runner=cast(Any, object()),
            circuit_breaker=cast(Any, circuit_breaker),
        ),
        orchestrator_engine=cast(Any, orchestrator),
        services=cast(Any, object()),
    )
    return container, orchestrator, circuit_breaker, queue, engine


@pytest.mark.asyncio
async def test_app_container_aclose_continues_after_close_failure(tmp_path: Path) -> None:
    container, orchestrator, circuit_breaker, queue, engine = _build_container(
        tmp_path=tmp_path,
        orchestrator_error=RuntimeError("orchestrator boom"),
    )

    with pytest.raises(RuntimeError, match="orchestrator boom"):
        await container.aclose()

    assert orchestrator.close_calls == 1
    assert circuit_breaker.close_calls == 1
    assert queue.close_calls == 1
    assert engine.dispose_calls == 1


def test_app_container_build_cleans_up_owned_resources_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _AsyncDisposable()
    fake_queue = _AsyncCloser()
    circuit_breaker = _AsyncCloser()

    monkeypatch.setattr(
        bootstrap_container,
        "_resolve_engine_and_session_factory",
        lambda **_: (cast(Any, engine), cast(Any, object()), True),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_uow_factory",
        lambda _session_factory: lambda: object(),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_repo_providers",
        lambda _settings: cast(Any, object()),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_queue",
        lambda _settings, *, metrics, queue=None: (cast(Any, fake_queue), True),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_builtin_registry",
        lambda *, settings: cast(Any, object()),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_tool_runner",
        lambda _settings, *, registry: cast(Any, object()),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_policy_gate",
        lambda _settings, *, metrics: cast(Any, object()),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_policy_runner",
        lambda **_: (cast(Any, object()), cast(Any, circuit_breaker)),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_planner",
        lambda _settings, *, registry, memory_loader: cast(Any, object()),
    )
    monkeypatch.setattr(
        bootstrap_container,
        "build_orchestrator_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap boom")),
    )
    monkeypatch.setattr(bootstrap_container, "configure_tracing", lambda _settings: None)

    with pytest.raises(RuntimeError, match="bootstrap boom"):
        AppContainer.build(settings=ReflexorSettings(workspace_root=tmp_path))

    assert circuit_breaker.close_calls == 1
    assert fake_queue.close_calls == 1
    assert engine.dispose_calls == 1
