from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent_harness.persistence.query_service import AgentRunQueryService
from app.agent_harness.persistence.schemas import AgentRunTimelineResponse
from app.api.dependencies import get_agent_run_query_service, get_database_manager, get_db_session
from app.infra.database import DatabaseManager


router = APIRouter(prefix="/agents", tags=["agents"])

TERMINAL_AGENT_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled", "deadletter", "dead_letter"}


@router.get("/runs/latest", response_model=AgentRunTimelineResponse)
def get_latest_agent_run(
    agent_key: str = Query(min_length=1),
    related_skill_definition_id: str = Query(min_length=1),
    session: Session = Depends(get_db_session),
    service: AgentRunQueryService = Depends(get_agent_run_query_service),
) -> AgentRunTimelineResponse:
    return service.get_latest_run_timeline(
        session,
        agent_key=agent_key,
        related_skill_definition_id=related_skill_definition_id,
    )


@router.get("/runs/{agent_run_id}", response_model=AgentRunTimelineResponse)
def get_agent_run(
    agent_run_id: str,
    session: Session = Depends(get_db_session),
    service: AgentRunQueryService = Depends(get_agent_run_query_service),
) -> AgentRunTimelineResponse:
    return service.get_run_timeline(session, agent_run_id)


@router.get("/runs/{agent_run_id}/timeline", response_model=AgentRunTimelineResponse)
def get_agent_run_timeline(
    agent_run_id: str,
    session: Session = Depends(get_db_session),
    service: AgentRunQueryService = Depends(get_agent_run_query_service),
) -> AgentRunTimelineResponse:
    return service.get_run_timeline(session, agent_run_id)


@router.get("/runs/{agent_run_id}/events")
async def stream_agent_run_events(
    agent_run_id: str,
    request: Request,
    database_manager: DatabaseManager = Depends(get_database_manager),
    service: AgentRunQueryService = Depends(get_agent_run_query_service),
) -> StreamingResponse:
    with database_manager.session() as session:
        service.get_run_timeline(session, agent_run_id)

    async def event_stream():
        last_snapshot = ""
        last_step_count = 0
        last_token_usage = ""
        snapshot_sent = False

        while not await request.is_disconnected():
            with database_manager.session() as session:
                timeline = service.get_run_timeline(session, agent_run_id)
            payload = timeline.model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

            if not snapshot_sent:
                yield _sse_event("snapshot", payload)
                snapshot_sent = True
            elif encoded != last_snapshot:
                yield _sse_event("progress", payload)

            steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
            for step in steps[last_step_count:]:
                yield _sse_event("step", step)
            last_step_count = len(steps)

            token_usage = payload.get("token_usage") or {}
            token_encoded = json.dumps(token_usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if token_usage and token_encoded != last_token_usage:
                yield _sse_event("token_usage", token_usage)
            last_token_usage = token_encoded
            last_snapshot = encoded

            if timeline.status in TERMINAL_AGENT_RUN_STATUSES:
                yield _sse_event("final" if timeline.status == "succeeded" else "error", payload)
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_event(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"
