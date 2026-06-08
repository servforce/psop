from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_job_query_service, get_memory_service
from app.jobs.schemas import RuntimeJobResponse
from app.jobs.service import JobQueryService
from app.memory.schemas import (
    MemoryEntryResponse,
    MemorySearchRequest,
    QueueMemoryCompactionRequest,
    UpdateMemoryEntryRequest,
)
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


@router.get("/{memory_id}", response_model=MemoryEntryResponse)
def get_memory_entry(
    memory_id: str,
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryEntryResponse:
    return service.get_entry(session, memory_id)


@router.post("/compactions/queue", response_model=RuntimeJobResponse, status_code=status.HTTP_202_ACCEPTED)
def queue_memory_compaction(
    payload: QueueMemoryCompactionRequest,
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
    job_query_service: JobQueryService = Depends(get_job_query_service),
) -> RuntimeJobResponse:
    job_id = service.enqueue_memory_compaction_job(session, payload)
    return job_query_service.get_runtime_job(session, job_id)


@router.patch("/{memory_id}", response_model=MemoryEntryResponse)
def update_memory_entry(
    memory_id: str,
    payload: UpdateMemoryEntryRequest,
    session: Session = Depends(get_db_session),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryEntryResponse:
    return service.update_entry(session, memory_id, payload)
