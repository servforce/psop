from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentPlan:
    objective: str
    steps: list[dict[str, Any]]
    exit_conditions: list[str]
    memory_entry_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "steps": self.steps,
            "exit_conditions": self.exit_conditions,
            "memory_entry_ids": self.memory_entry_ids,
        }


class AgentPlanner:
    """Build a deterministic execution plan for the current AgentRun turn."""

    def create_plan(
        self,
        *,
        agent_key: str,
        spec: dict[str, Any],
        input_payload: dict[str, Any],
        active_skill_names: list[str],
        memory_context: list[dict[str, Any]],
    ) -> AgentPlan:
        objective = str(spec.get("goal") or input_payload.get("goal") or agent_key)
        decision_hint = "agent_decision" if "agent_decision" in input_payload else "expected_output"
        steps = [
            {
                "id": "hydrate_skills",
                "kind": "skills",
                "summary": "Activate allowed Skill packages and collect tool permissions.",
                "skill_names": active_skill_names,
            },
            {
                "id": "retrieve_memory",
                "kind": "memory",
                "summary": "Retrieve active Agent memory as optional context, not Runtime state.",
                "memory_entry_count": len(memory_context),
            },
            {
                "id": "complete_model_decision",
                "kind": "model",
                "summary": "Produce an AgentDecision using the active spec, memory context and input payload.",
                "decision_hint": decision_hint,
            },
            {
                "id": "apply_guardrails",
                "kind": "guardrails",
                "summary": "Validate final output or tool call before state transitions.",
            },
        ]
        return AgentPlan(
            objective=objective,
            steps=steps,
            exit_conditions=[
                "final_output accepted by output guardrail",
                "tool_call completed or waiting_tool_authorization",
                "fail decision recorded",
            ],
            memory_entry_ids=[str(item.get("id")) for item in memory_context if item.get("id")],
        )
