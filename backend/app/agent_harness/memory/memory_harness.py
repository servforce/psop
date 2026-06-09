from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.memory.schemas import MemorySearchRequest
from app.memory.service import MemoryService
from app.pskills.exceptions import SkillValidationError


class AgentMemoryHarness:
    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self.memory_service = memory_service or MemoryService()

    def retrieve_context(
        self,
        session: Session,
        *,
        agent_key: str,
        spec: dict[str, Any],
    ) -> list[dict[str, object]]:
        return self.memory_service.retrieve_context_for_agent(
            session,
            agent_key=agent_key,
            limit=self.context_limit(spec),
        )

    def search_tool(self, session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = MemorySearchRequest(
                query=str(arguments.get("query") or arguments.get("q") or ""),
                namespace=arguments.get("namespace"),
                memory_type=arguments.get("memory_type"),
                status=arguments.get("status", "active"),
                agent_key=arguments.get("agent_key"),
                limit=int(arguments.get("limit") or 25),
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise SkillValidationError(
                "psop.memory.search 参数无效。",
                details={"arguments_summary": arguments, "error": str(error)},
            ) from error
        entries = self.memory_service.search(session, payload)
        return {
            "memory_entry_count": len(entries),
            "memory_entry_ids": [item.id for item in entries],
            "entries": [item.model_dump(mode="json") for item in entries],
        }

    def write_candidates(
        self,
        session: Session,
        *,
        agent_key: str,
        agent_run_id: str,
        candidates: list[dict[str, Any]],
        commit: bool = False,
    ) -> list[Any]:
        try:
            return self.memory_service.write_candidates(
                session,
                agent_key=agent_key,
                created_by_agent_run_id=agent_run_id,
                candidates=candidates,
                commit=commit,
            )
        except ValidationError as error:
            raise SkillValidationError(
                "psop.memory.write_candidate 参数无效。",
                details={"error": str(error)},
            ) from error

    @staticmethod
    def context_limit(spec: dict[str, Any]) -> int:
        policy = spec.get("memory_policy")
        if not isinstance(policy, dict):
            return 5
        try:
            raw_limit = policy.get("context_limit", 5)
            if raw_limit is None:
                raw_limit = 5
            return max(1, min(20, int(raw_limit)))
        except (TypeError, ValueError):
            return 5
