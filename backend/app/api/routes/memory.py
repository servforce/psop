from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_memory_service
from app.memory.schemas import MemoryEntryResponse, MemorySearchRequest, UpdateMemoryEntryRequest
from app.memory.service import MemoryService


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[MemoryEntryResponse])
def list_memory_entries(
    namespace: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
) -> list[MemoryEntryResponse]:
    return service.list_entries(
        session,
        namespace=namespace,
        memory_type=memory_type,
        status=status,
        agent_key=agent_key,
        q=q,
        limit=limit,
    )


@router.post("/search", response_model=list[MemoryEntryResponse])
def search_memory(
    payload: MemorySearchRequest,
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
) -> list[MemoryEntryResponse]:
    return service.search(session, payload)


@router.patch("/{memory_id}", response_model=MemoryEntryResponse)
def update_memory_entry(
    memory_id: str,
    payload: UpdateMemoryEntryRequest,
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryEntryResponse:
    return service.update_entry(session, memory_id, payload)
