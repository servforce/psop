from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.memory.models import AgentMemoryEntry


class MemoryRepository:
    """Database access for Agent memory entries."""

    def get_entry(self, session: Session, memory_id: str) -> AgentMemoryEntry | None:
        return session.get(AgentMemoryEntry, memory_id)

    def list_entries(
        self,
        session: Session,
        *,
        namespace: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        agent_key: str | None = None,
        q: str | None = None,
        limit: int = 100,
    ) -> list[AgentMemoryEntry]:
        query = select(AgentMemoryEntry)
        if namespace:
            query = query.where(AgentMemoryEntry.namespace == namespace)
        if memory_type:
            query = query.where(AgentMemoryEntry.memory_type == memory_type)
        if status:
            query = query.where(AgentMemoryEntry.status == status)
        if agent_key:
            query = query.where(AgentMemoryEntry.agent_key == agent_key)
        if q:
            pattern = f"%{q}%"
            query = query.where(or_(AgentMemoryEntry.title.ilike(pattern), AgentMemoryEntry.content.ilike(pattern)))
        query = query.order_by(AgentMemoryEntry.created_at.desc(), AgentMemoryEntry.id.desc()).limit(limit)
        return list(session.scalars(query).all())
