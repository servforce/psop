from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.models import AgentDefinition, AgentToolCall
from app.tools.models import ToolDefinition


class ToolRepository:
    """Database access for the tool registry."""

    def get_tool_by_name(self, session: Session, tool_name: str) -> ToolDefinition | None:
        return session.scalar(select(ToolDefinition).where(ToolDefinition.name == tool_name))

    def list_tools(
        self,
        session: Session,
        *,
        side_effect_level: str | None = None,
        requires_authorization: bool | None = None,
        status: str | None = None,
    ) -> list[ToolDefinition]:
        query = select(ToolDefinition).order_by(ToolDefinition.name.asc())
        if side_effect_level:
            query = query.where(ToolDefinition.side_effect_level == side_effect_level)
        if requires_authorization is not None:
            query = query.where(ToolDefinition.requires_authorization == requires_authorization)
        if status:
            query = query.where(ToolDefinition.status == status)
        return list(session.scalars(query).all())

    def list_agent_definitions(self, session: Session) -> list[AgentDefinition]:
        return list(session.scalars(select(AgentDefinition).order_by(AgentDefinition.key.asc())).all())

    def count_tool_calls(self, session: Session, tool_name: str) -> int:
        return int(
            session.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.tool_name == tool_name)) or 0
        )

    def count_failed_tool_calls(self, session: Session, tool_name: str) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AgentToolCall)
                .where(
                    AgentToolCall.tool_name == tool_name,
                    AgentToolCall.status.in_(["failed", "blocked", "denied"]),
                )
            )
            or 0
        )
