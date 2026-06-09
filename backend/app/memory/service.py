from __future__ import annotations

from sqlalchemy.orm import Session

from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import MEMORY_COMPACTION_JOB_TYPE
from app.memory.models import AgentMemoryEntry
from app.memory.policy import (
    VALID_MEMORY_STATUSES,
    VALID_MEMORY_TYPES,
    memory_boundary_metadata,
    normalize_memory_status,
    normalize_memory_type,
    normalize_source_refs,
)
from app.memory.repository import MemoryRepository
from app.memory.schemas import (
    MemoryCandidate,
    MemoryEntryResponse,
    MemorySearchRequest,
    QueueMemoryCompactionRequest,
    UpdateMemoryEntryRequest,
)
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import generate_uuid, now_utc


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.repository = repository or MemoryRepository()
        self.job_repository = job_repository or JobRepository()

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

    def list_entries_for_agent_run(
        self,
        session: Session,
        agent_run_id: str,
        *,
        limit: int = 100,
    ) -> list[MemoryEntryResponse]:
        return [
            self._build_entry_response(item)
            for item in self.repository.list_entries(
                session,
                created_by_agent_run_id=agent_run_id,
                limit=max(1, min(200, int(limit or 100))),
            )
        ]

    def get_entry(self, session: Session, memory_id: str) -> MemoryEntryResponse:
        return self._build_entry_response(self._get_entry(session, memory_id))

    def retrieve_context_for_agent(
        self,
        session: Session,
        *,
        agent_key: str,
        limit: int = 5,
    ) -> list[dict[str, object]]:
        entries = self.repository.list_entries(
            session,
            status="active",
            agent_key=agent_key,
            limit=max(1, min(20, int(limit or 5))),
        )
        return [
            {
                "id": item.id,
                "namespace": item.namespace,
                "memory_type": item.memory_type,
                "title": item.title,
                "content": item.content[:500],
                "confidence": item.confidence,
                "source_refs": list(item.source_refs or []),
                "tags": list(item.tags or []),
                "used_as_runtime_state": False,
                "formal_source_refs": list((item.metadata_json or {}).get("formal_source_refs") or []),
            }
            for item in entries
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
            status = normalize_memory_status(payload.status)
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
            entry.source_refs = normalize_source_refs(list(payload.source_refs))
            entry.metadata_json = memory_boundary_metadata(entry.source_refs, dict(entry.metadata_json or {}))
        if payload.tags is not None:
            entry.tags = [str(item).strip() for item in payload.tags if str(item).strip()]
        if payload.metadata is not None:
            entry.metadata_json = memory_boundary_metadata(list(entry.source_refs or []), dict(payload.metadata))
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
            memory_type = normalize_memory_type(candidate.memory_type)
            content = candidate.content.strip()
            if not content:
                continue
            source_refs = normalize_source_refs(list(candidate.source_refs))
            metadata = memory_boundary_metadata(source_refs, dict(candidate.metadata))
            entry = AgentMemoryEntry(
                namespace=candidate.namespace.strip() or "default",
                memory_type=memory_type,
                agent_key=agent_key,
                status="pending_review",
                confidence=candidate.confidence,
                title=candidate.title.strip() or content[:120],
                content=content,
                source_refs=source_refs,
                tags=[str(tag).strip() for tag in candidate.tags if str(tag).strip()],
                metadata_json=metadata,
                created_by_agent_run_id=created_by_agent_run_id,
            )
            session.add(entry)
            entries.append(entry)
        session.flush()
        if commit:
            session.commit()
        return [self._build_entry_response(item) for item in entries]

    def enqueue_memory_compaction_job(self, session: Session, payload: QueueMemoryCompactionRequest) -> str:
        self._validate_optional_filter("memory_type", payload.memory_type, VALID_MEMORY_TYPES)
        self._validate_optional_filter("status", payload.status, VALID_MEMORY_STATUSES)
        target_memory_type = normalize_memory_type(payload.target_memory_type.strip() or "artifact")
        target_status = normalize_memory_status(payload.target_status.strip() or "pending_review")

        idempotency_key = self._normalize_optional(payload.idempotency_key) or generate_uuid()
        dedupe_key = f"job:memory-compaction:{idempotency_key}"
        existing = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing.id

        job_payload = payload.model_dump(mode="json", exclude_none=True)
        job_payload.update(
            {
                "operation": "memory_compaction",
                "idempotency_key": idempotency_key,
                "limit": max(1, min(200, int(payload.limit or 50))),
                "target_memory_type": target_memory_type,
                "target_status": target_status,
                "archive_source_entries": bool(payload.archive_source_entries),
            }
        )
        job = RuntimeJob(
            job_type=MEMORY_COMPACTION_JOB_TYPE,
            status="pending",
            payload=job_payload,
            dedupe_key=dedupe_key,
        )
        session.add(job)
        session.commit()
        return job.id

    def process_memory_compaction_job(self, session: Session, job_id: str) -> MemoryEntryResponse:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 Memory compaction 任务。", details={"job_id": job_id})
        if job.job_type != MEMORY_COMPACTION_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 Memory compaction 任务。", details={"job_type": job.job_type})

        payload = dict(job.payload or {})
        compacted_memory_id = str(payload.get("compacted_memory_id") or "").strip()
        if compacted_memory_id:
            compacted = self._get_entry(session, compacted_memory_id)
        else:
            sources = self.repository.list_entries(
                session,
                namespace=self._normalize_optional(payload.get("namespace")),
                memory_type=self._normalize_optional(payload.get("memory_type")),
                status=self._normalize_optional(payload.get("status")) or "active",
                agent_key=self._normalize_optional(payload.get("agent_key")),
                limit=max(1, min(200, int(payload.get("limit") or 50))),
            )
            if not sources:
                raise SkillValidationError("Memory compaction 没有找到可压缩条目。", details={"job_id": job.id})
            compacted = self._create_compacted_memory_entry(session, sources=sources, payload=payload)
            if bool(payload.get("archive_source_entries")):
                now = now_utc()
                for source in sources:
                    if source.id == compacted.id:
                        continue
                    source.status = "archived"
                    source.reviewed_at = now
                    source.updated_at = now
            session.flush()

        metrics = dict(job.metrics or {})
        source_refs = list(compacted.source_refs or [])
        source_count = len([item for item in source_refs if item.get("kind") == "agent_memory_entry"])
        metrics.update(
            {
                "compacted_memory_id": compacted.id,
                "source_memory_count": source_count,
                "target_namespace": compacted.namespace,
                "target_memory_type": compacted.memory_type,
            }
        )
        job.payload = {
            **payload,
            "operation": "memory_compaction",
            "compacted_memory_id": compacted.id,
            "target_namespace": compacted.namespace,
            "target_memory_type": compacted.memory_type,
            "source_memory_count": source_count,
            "archive_source_entries": bool(payload.get("archive_source_entries")),
        }
        job.metrics = metrics
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        session.commit()
        return self._build_entry_response(compacted)

    def _create_compacted_memory_entry(
        self,
        session: Session,
        *,
        sources: list[AgentMemoryEntry],
        payload: dict,
    ) -> AgentMemoryEntry:
        target_namespace = str(payload.get("target_namespace") or payload.get("namespace") or sources[0].namespace).strip()
        target_memory_type = normalize_memory_type(str(payload.get("target_memory_type") or "artifact").strip())
        target_status = normalize_memory_status(str(payload.get("target_status") or "pending_review").strip())
        title = str(payload.get("title") or "").strip() or f"Compacted memory: {target_namespace}/{target_memory_type}"
        source_refs = [
            {
                "kind": "agent_memory_entry",
                "id": source.id,
                "namespace": source.namespace,
                "memory_type": source.memory_type,
                "status": source.status,
            }
            for source in sources
        ]
        content = self._compact_memory_content(sources)
        confidence_values = [source.confidence for source in sources if isinstance(source.confidence, int)]
        confidence = int(round(sum(confidence_values) / len(confidence_values))) if confidence_values else 50
        tags = sorted({tag for source in sources for tag in list(source.tags or []) if str(tag).strip()} | {"compacted"})
        compacted = AgentMemoryEntry(
            namespace=target_namespace or "default",
            memory_type=target_memory_type,
            agent_key=str(payload.get("target_agent_key") or payload.get("agent_key") or sources[0].agent_key or "").strip(),
            status=target_status,
            confidence=max(0, min(100, confidence)),
            title=title,
            content=content,
            source_refs=source_refs,
            tags=tags,
            metadata_json={
                "schema": "psop-memory-compaction/v1",
                "source_memory_count": len(sources),
                "source_namespaces": sorted({source.namespace for source in sources}),
                "source_memory_types": sorted({source.memory_type for source in sources}),
            },
            created_by_agent_run_id=self._normalize_optional(payload.get("created_by_agent_run_id")),
        )
        session.add(compacted)
        return compacted

    @staticmethod
    def _compact_memory_content(sources: list[AgentMemoryEntry]) -> str:
        lines = ["Compacted memory summary:"]
        for index, source in enumerate(sources, start=1):
            content = " ".join(str(source.content or "").split())
            if len(content) > 500:
                content = f"{content[:497]}..."
            lines.append(f"{index}. [{source.memory_type}/{source.status}] {source.title}: {content}")
        return "\n".join(lines)

    def _get_entry(self, session: Session, memory_id: str) -> AgentMemoryEntry:
        entry = self.repository.get_entry(session, memory_id)
        if not entry:
            raise SkillNotFoundError("未找到 Memory。", details={"memory_id": memory_id})
        return entry

    @staticmethod
    def _validate_memory_type(memory_type: str) -> None:
        normalize_memory_type(memory_type)

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
