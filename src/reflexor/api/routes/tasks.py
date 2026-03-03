from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from reflexor.api.deps import ContainerDep

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(_container: ContainerDep) -> dict[str, object]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


__all__ = ["router"]
