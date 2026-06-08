from __future__ import annotations

from typing import Any


def finding_source_ref(finding: Any, evaluation: Any | None = None) -> dict[str, Any]:
    return {
        "kind": "run_evaluation_finding",
        "id": getattr(finding, "id", ""),
        "evaluation_id": getattr(finding, "evaluation_id", ""),
        "source_finding_id": getattr(finding, "id", ""),
        "source_evaluation_id": getattr(finding, "evaluation_id", ""),
        "source_run_id": getattr(evaluation, "run_id", "") if evaluation else "",
    }


def finding_evidence_refs(finding: Any, evaluation: Any | None = None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in list(getattr(finding, "evidence_refs", None) or []):
        if not isinstance(ref, dict):
            continue
        normalized = dict(ref)
        if not normalized.get("source_finding_id"):
            normalized["source_finding_id"] = getattr(finding, "id", "")
        if not normalized.get("source_evaluation_id"):
            normalized["source_evaluation_id"] = getattr(finding, "evaluation_id", "")
        run_id = getattr(evaluation, "run_id", "") if evaluation else ""
        if run_id and not normalized.get("source_run_id"):
            normalized["source_run_id"] = run_id
        refs.append(normalized)
    return refs
