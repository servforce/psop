from __future__ import annotations

from sqlalchemy.orm import Session

from app.memory.models import AgentMemoryEntry
from app.memory.repository import MemoryRepository
from app.memory.schemas import (
    MemoryCandidate,
    MemoryEntryResponse,
    MemorySearchRequest,
    UpdateMemoryEntryRequest,
)
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc


VALID_MEMORY_TYPES = {"short_term", "semantic", "episodic", "procedural", "artifact"}
VALID_MEMORY_STATUSES = {"pending_review", "active", "rejected", "archived"}


class MemoryService:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

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
    ) -> list[MemoryEntryResponse]:
        self._validate_optional_filter("memory_type", memory_type, VALID_MEMORY_TYPES)
        self._validate_optional_filter("status", status, VALID_MEMORY_STATUSES)
        return [
            self._build_entry_response(item)
            for item in self.repository.list_entries(
                session,
                namespace=self._normalize_optional(namespace),
                memory_type=self._normalize_optional(memory_type),
                status=self._normalize_optional(status),
                agent_key=self._normalize_optional(agent_key),
                q=self._normalize_optional(q),
                limit=max(1, min(200, int(limit or 100))),
            )
        ]

    def search(self, session: Session, payload: MemorySearchRequest) -> list[MemoryEntryResponse]:
        query = payload.query.strip()
        status = payload.status.strip() if payload.status else None
        return self.list_entries(
            session,
            namespace=payload.namespace,
            memory_type=payload.memory_type,
            status=status,
            agent_key=payload.agent_key,
            q=query or None,
            limit=payload.limit,
        )

    def update_entry(
        self,
        session: Session,
        memory_id: str,
        payload: UpdateMemoryEntryRequest,
    ) -> MemoryEntryResponse:
        entry = self._get_entry(session, memory_id)
        if payload.status is not None:
            status = payload.status.strip()
            if status not in VALID_MEMORY_STATUSES:
                raise SkillValidationError("memory status 无效。", details={"status": status})
            entry.status = status
            if status in {"active", "rejected", "archived"}:
                entry.reviewed_at = now_utc()
                entry.reviewed_by_agent_run_id = payload.reviewed_by_agent_run_id
        if payload.title is not None:
            entry.title = payload.title.strip()
        if payload.content is not None:
            content = payload.content.strip()
            if not content:
                raise SkillValidationError("memory content 不能为空。")
            entry.content = content
        if payload.confidence is not None:
            entry.confidence = int(payload.confidence)
        if payload.source_refs is not None:
            entry.source_refs = list(payload.source_refs)
        if payload.tags is not None:
            entry.tags = [str(item).strip() for item in payload.tags if str(item).strip()]
        if payload.metadata is not None:
            entry.metadata_json = dict(payload.metadata)
        entry.updated_at = now_utc()
        session.commit()
        return self._build_entry_response(entry)

    def write_candidates(
        self,
        session: Session,
        *,
        agent_key: str,
        created_by_agent_run_id: str | None,
        candidates: list[MemoryCandidate | dict],
        commit: bool = True,
    ) -> list[MemoryEntryResponse]:
        entries: list[AgentMemoryEntry] = []
        for item in candidates:
            candidate = item if isinstance(item, MemoryCandidate) else MemoryCandidate(**item)
            self._validate_memory_type(candidate.memory_type)
            content = candidate.content.strip()
            if not content:
                continue
            entry = AgentMemoryEntry(
                namespace=candidate.namespace.strip() or "default",
                memory_type=candidate.memory_type.strip(),
                agent_key=agent_key,
                status="pending_review",
                confidence=candidate.confidence,
                title=candidate.title.strip() or content[:120],
                content=content,
                source_refs=list(candidate.source_refs),
                tags=[str(tag).strip() for tag in candidate.tags if str(tag).strip()],
                metadata_json=dict(candidate.metadata),
                created_by_agent_run_id=created_by_agent_run_id,
            )
            session.add(entry)
            entries.append(entry)
        session.flush()
        if commit:
            session.commit()
        return [self._build_entry_response(item) for item in entries]

    def _get_entry(self, session: Session, memory_id: str) -> AgentMemoryEntry:
        entry = self.repository.get_entry(session, memory_id)
        if not entry:
            raise SkillNotFoundError("未找到 Memory。", details={"memory_id": memory_id})
        return entry

    @staticmethod
    def _validate_memory_type(memory_type: str) -> None:
        if memory_type not in VALID_MEMORY_TYPES:
            raise SkillValidationError("memory_type 无效。", details={"memory_type": memory_type})

    @staticmethod
    def _validate_optional_filter(name: str, value: str | None, allowed: set[str]) -> None:
        if value is not None and value.strip() and value.strip() not in allowed:
            raise SkillValidationError(f"{name} filter 无效。", details={name: value})

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _build_entry_response(entry: AgentMemoryEntry) -> MemoryEntryResponse:
        return MemoryEntryResponse(
            id=entry.id,
            namespace=entry.namespace,
            memory_type=entry.memory_type,
            agent_key=entry.agent_key,
            status=entry.status,
            confidence=entry.confidence,
            title=entry.title,
            content=entry.content,
            source_refs=list(entry.source_refs or []),
            tags=list(entry.tags or []),
            metadata=entry.metadata_json,
            created_by_agent_run_id=entry.created_by_agent_run_id,
            reviewed_by_agent_run_id=entry.reviewed_by_agent_run_id,
            reviewed_at=entry.reviewed_at,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
