from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_tool_service
from app.tools.schemas import ToolDefinitionResponse, ToolTestRequest, ToolTestResponse
from app.tools.service import ToolService


router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolDefinitionResponse])
def list_tools(
    side_effect_level: str | None = Query(default=None),
    requires_authorization: bool | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: ToolService = Depends(get_tool_service),
) -> list[ToolDefinitionResponse]:
    return service.list_tools(
        session,
        side_effect_level=side_effect_level,
        requires_authorization=requires_authorization,
        status=status,
    )


@router.get("/{tool_name}", response_model=ToolDefinitionResponse)
def get_tool(
    tool_name: str,
    session: Session = Depends(get_db_session),
    service: ToolService = Depends(get_tool_service),
) -> ToolDefinitionResponse:
    return service.get_tool(session, tool_name)


@router.post("/{tool_name}/test", response_model=ToolTestResponse)
def test_tool(
    tool_name: str,
    payload: ToolTestRequest,
    session: Session = Depends(get_db_session),
    service: ToolService = Depends(get_tool_service),
) -> ToolTestResponse:
    return service.test_tool(session, tool_name, payload)
