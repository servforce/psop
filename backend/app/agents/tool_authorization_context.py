from __future__ import annotations

from typing import Any


BUSINESS_CONTEXT_ALIASES: dict[str, tuple[str, ...]] = {
    "proposal_id": ("proposal_id", "governance_proposal_id"),
    "experiment_id": ("experiment_id", "governance_experiment_id"),
    "source_evaluation_id": ("source_evaluation_id", "evaluation_id", "evaluation_report_id", "run_evaluation_id"),
    "source_finding_id": ("source_finding_id", "source_finding_ids", "finding_id", "finding_ids", "run_evaluation_finding_id"),
    "source_run_id": ("source_run_id", "run_id"),
    "run_trace_id": ("run_trace_id", "trace_id", "trace_event_id"),
    "run_event_id": ("run_event_id", "event_id"),
    "snapshot_seq": ("snapshot_seq", "session_token_seq", "seq_no"),
    "pskill_definition_id": ("pskill_definition_id", "pskill_id", "skill_id"),
    "package_name": ("package_name", "skill_package", "skill_package_name"),
    "agent_key": ("agent_key",),
    "memory_id": ("memory_id", "memory_entry_id"),
}


def agent_run_business_context(agent_run: Any) -> dict[str, Any]:
    owner_type = str(getattr(agent_run, "owner_type", "") or "").strip()
    owner_id = str(getattr(agent_run, "owner_id", "") or "").strip()
    run_id = str(getattr(agent_run, "run_id", "") or "").strip()
    context = _compact(
        {
            "agent_run_id": getattr(agent_run, "id", ""),
            "agent_key": getattr(agent_run, "agent_key", ""),
            "agent_owner_type": owner_type,
            "agent_owner_id": owner_id,
            "source_run_id": run_id,
        }
    )
    if owner_type in {"governance", "governance_proposal", "psop_improvement_proposal"}:
        context["proposal_id"] = owner_id
    elif owner_type == "run_evaluation":
        context["source_evaluation_id"] = owner_id
    elif owner_type in {"runtime", "runtime_run", "run"} and owner_id:
        context.setdefault("source_run_id", owner_id)
    elif owner_type == "compile_request":
        context["compile_request_id"] = owner_id
    elif owner_type == "pskill_test_run":
        context["test_run_id"] = owner_id
    elif owner_type == "pskill_draft":
        context["pskill_draft_id"] = owner_id
    return merge_business_context(
        context,
        _derive_context_from_nested(getattr(agent_run, "input_payload", None)),
        _derive_context_from_nested(getattr(agent_run, "output_payload", None)),
    )


def enrich_tool_authorization_request_payload(
    request_payload: dict[str, Any],
    *,
    agent_run: Any,
) -> dict[str, Any]:
    payload = dict(request_payload or {})
    payload["business_context"] = merge_business_context(
        payload.get("business_context") if isinstance(payload.get("business_context"), dict) else {},
        agent_run_business_context(agent_run),
        _derive_context_from_nested(payload),
    )
    return payload


def tool_authorization_business_context(authorization: Any) -> dict[str, Any]:
    request_payload = getattr(authorization, "request_payload", None)
    response_payload = getattr(authorization, "response_payload", None)
    tool_arguments_summary = getattr(authorization, "tool_arguments_summary", None)
    base = {
        "authorization_id": getattr(authorization, "id", ""),
        "agent_run_id": getattr(authorization, "agent_run_id", ""),
        "agent_tool_call_id": getattr(authorization, "agent_tool_call_id", ""),
        "source_run_id": getattr(authorization, "run_id", ""),
        "run_event_id": getattr(authorization, "run_event_id", ""),
        "tool_name": getattr(authorization, "tool_name", ""),
        "tool_provider": getattr(authorization, "tool_provider", ""),
        "side_effect_level": getattr(authorization, "side_effect_level", ""),
        "risk_level": getattr(authorization, "risk_level", ""),
    }
    embedded = {}
    if isinstance(request_payload, dict) and isinstance(request_payload.get("business_context"), dict):
        embedded = request_payload["business_context"]
    return merge_business_context(
        base,
        embedded,
        _derive_context_from_nested(request_payload),
        _derive_context_from_nested(tool_arguments_summary),
        _derive_context_from_nested(response_payload),
    )


def merge_business_context(*contexts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for context in contexts:
        if not isinstance(context, dict):
            continue
        for key, value in context.items():
            if _has_value(value) and not _has_value(merged.get(key)):
                merged[key] = value
    return merged


def _derive_context_from_nested(value: Any) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for canonical_key, aliases in BUSINESS_CONTEXT_ALIASES.items():
        found = _first_nested_value(value, aliases)
        if _has_value(found):
            context[canonical_key] = _normalize_context_value(found)
    return context


def _first_nested_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if _has_value(value.get(key)):
                return value[key]
        for item in value.values():
            found = _first_nested_value(item, keys)
            if _has_value(found):
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_nested_value(item, keys)
            if _has_value(found):
                return found
    return None


def _compact(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if _has_value(value)}


def _normalize_context_value(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            if _has_value(item) and not isinstance(item, (dict, list)):
                return item
        return ""
    return value


def _has_value(value: Any) -> bool:
    return value is not None and value != ""
