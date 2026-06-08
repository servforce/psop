from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    namespace: str = Field(default="default", max_length=160)
    memory_type: str = Field(min_length=2, max_length=40)
    title: str = Field(default="", max_length=255)
    content: str = Field(min_length=1)
    confidence: int = Field(default=50, ge=0, le=100)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    query: str = Field(default="", max_length=500)
    namespace: str | None = Field(default=None, max_length=160)
    memory_type: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default="active", max_length=40)
    agent_key: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=25, ge=1, le=200)


class UpdateMemoryEntryRequest(BaseModel):
    status: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=255)
    content: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    source_refs: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    reviewed_by_agent_run_id: str | None = None


class QueueMemoryCompactionRequest(BaseModel):
    namespace: str | None = Field(default=None, max_length=160)
    memory_type: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default="active", max_length=40)
    agent_key: str | None = Field(default=None, max_length=160)
    limit: int = Field(default=50, ge=1, le=200)
    target_namespace: str | None = Field(default=None, max_length=160)
    target_memory_type: str = Field(default="artifact", max_length=40)
    target_status: str = Field(default="pending_review", max_length=40)
    target_agent_key: str | None = Field(default=None, max_length=160)
    title: str | None = Field(default=None, max_length=255)
    archive_source_entries: bool = False
    created_by_agent_run_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=160)


class MemoryEntryResponse(BaseModel):
    id: str
    namespace: str
    memory_type: str
    agent_key: str
    status: str
    confidence: int
    title: str
    content: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any]
    created_by_agent_run_id: str | None = None
    reviewed_by_agent_run_id: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
