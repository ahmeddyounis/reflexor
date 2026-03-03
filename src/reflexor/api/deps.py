"""API dependency injection helpers.

Routes should depend on narrow, typed dependencies (e.g. a container) and avoid touching database
sessions directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from reflexor.config import ReflexorSettings
from reflexor.infra.db.engine import AsyncSessionFactory
from reflexor.orchestrator.queue import Queue


@dataclass(frozen=True, slots=True)
class ApiContainer:
    """API runtime container stored on `app.state.container`."""

    settings: ReflexorSettings
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    queue: Queue


def get_container(request: Request) -> ApiContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("API container is not initialized (lifespan not running)")
    return cast(ApiContainer, container)


ContainerDep = Annotated[ApiContainer, Depends(get_container)]


__all__ = ["ApiContainer", "ContainerDep", "get_container"]
