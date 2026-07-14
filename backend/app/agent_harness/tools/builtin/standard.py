from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec
from app.core.config import Settings


_STANDARD_RESULTS_CONTEXT_KEY = "_psop_builder_standard_results"
_STANDARD_REF_PATTERN = re.compile(r"\b(?:GB(?:/T)?|AQ(?:/T)?|DL(?:/T)?|SY(?:/T)?|HG(?:/T)?|JB(?:/T)?|NB(?:/T)?)\s*[0-9][0-9A-Za-z./-]*")
_CLAUSE_REF_PATTERN = re.compile(r"(?:第\s*)?[0-9]+(?:\.[0-9]+){1,}(?:\s*条)?|第\s*[一二三四五六七八九十百]+条")


def register_standard_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="psop.standard.search",
            description="检索平台 LightRAG 中的国家或行业标准片段。",
            purpose="用于 psop.builder 查找与任务、设备、风险和安全动作相关的可追溯标准参考。",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 2, "maxLength": 500},
                    "task_summary": {"type": "string", "maxLength": 1000},
                    "jurisdiction": {"type": "string", "maxLength": 32},
                    "standard_scope": {
                        "type": "string",
                        "enum": ["national", "industry", "local", "enterprise", "unknown"],
                    },
                    "hazard_types": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                    "equipment_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["success", "error"]},
                    "items": {"type": "array", "items": {"type": "object"}},
                },
            },
            risk_class="read_only",
            side_effect_class="none",
            resource_scope="standard_lightrag",
            permission_policy="allow_with_service_config",
            timeout_seconds=20.0,
            max_result_chars=16000,
            audit_event="agent.tool.standard_search",
            error_types=["invalid_arguments", "timeout", "service_unavailable", "internal_error"],
        ),
        _standard_search,
    )


def _standard_search(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    settings = context.settings if isinstance(context.settings, Settings) else Settings()
    try:
        query = _build_query(arguments)
        max_results = min(_bounded_int(arguments.get("max_results"), default=settings.standard_lightrag_max_results, minimum=1, maximum=8), settings.standard_lightrag_max_results)
    except Exception as exc:
        result = _error_result("invalid_arguments", str(exc), retryable=False)
        _record_standard_search_event(context, arguments, result, started_at)
        return result
    if not settings.standard_lightrag_base_url or not settings.standard_lightrag_api_key:
        result = _error_result("service_unavailable", "LightRAG 标准检索未配置。", retryable=False)
        _record_standard_search_event(context, arguments, result, started_at)
        return result
    request_payload = {
        "query": query,
        "mode": "mix",
        "include_references": True,
        "include_chunk_content": True,
        "stream": False,
        "response_type": "Bullet Points",
        "top_k": max_results,
        "chunk_top_k": max_results,
        "max_total_tokens": 6000,
    }
    try:
        response = httpx.post(
            _query_url(settings.standard_lightrag_base_url),
            headers={"X-API-Key": settings.standard_lightrag_api_key},
            json=request_payload,
            timeout=settings.standard_lightrag_timeout_seconds,
        )
        if response.status_code >= 400:
            result = _error_result("service_unavailable", f"LightRAG 标准检索返回 HTTP {response.status_code}。", retryable=response.status_code >= 500)
            _record_standard_search_event(context, arguments, result, started_at)
            return result
        payload = response.json()
    except httpx.TimeoutException:
        result = _error_result("timeout", "LightRAG 标准检索超时。", retryable=True)
        _record_standard_search_event(context, arguments, result, started_at)
        return result
    except Exception as exc:
        result = _error_result("internal_error", f"LightRAG 标准检索失败：{exc}", retryable=False)
        _record_standard_search_event(context, arguments, result, started_at)
        return result
    result = _normalize_query_response(query=query, payload=payload, max_results=max_results)
    context.invocation_context[_STANDARD_RESULTS_CONTEXT_KEY] = result.get("items", [])
    _record_standard_search_event(context, arguments, result, started_at)
    return result


def _build_query(arguments: dict[str, Any]) -> str:
    query = _require_str(arguments, "query")
    parts = [query]
    for key in ("task_summary", "jurisdiction", "standard_scope"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for key in ("hazard_types", "equipment_keywords"):
        value = arguments.get(key)
        if isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
    full_query = "；".join(parts)
    if len(full_query) < 3:
        raise ValueError("query 至少需要 3 个字符。")
    return full_query[:1000]


def _normalize_query_response(*, query: str, payload: dict[str, Any], max_results: int) -> dict[str, Any]:
    response_text = str(payload.get("response") or "").strip()
    references = payload.get("references") if isinstance(payload.get("references"), list) else []
    items = []
    for index, reference in enumerate(references[:max_results], start=1):
        if not isinstance(reference, dict):
            continue
        chunks = reference.get("content") if isinstance(reference.get("content"), list) else []
        snippet = _snippet(chunks, fallback=response_text)
        standard_ref = _first_match(_STANDARD_REF_PATTERN, " ".join([str(reference.get("file_path") or ""), snippet, response_text]))
        clause_ref = _first_match(_CLAUSE_REF_PATTERN, " ".join([snippet, response_text]))
        citation_status = "complete" if standard_ref and clause_ref else "incomplete"
        items.append(
            {
                "standard_ref": standard_ref,
                "title": _title_from_reference(reference),
                "issuing_authority": "",
                "clause_ref": clause_ref,
                "clause_title": "",
                "snippet": snippet,
                "relevance_summary": _truncate(response_text, 600),
                "retrieval_score": None,
                "source_uri": f"lightrag://query/{reference.get('reference_id') or index}",
                "reference_id": str(reference.get("reference_id") or index),
                "file_path": str(reference.get("file_path") or ""),
                "citation_status": citation_status,
            }
        )
    if not items and response_text:
        standard_ref = _first_match(_STANDARD_REF_PATTERN, response_text)
        clause_ref = _first_match(_CLAUSE_REF_PATTERN, response_text)
        items.append(
            {
                "standard_ref": standard_ref,
                "title": "",
                "issuing_authority": "",
                "clause_ref": clause_ref,
                "clause_title": "",
                "snippet": _truncate(response_text, 1200),
                "relevance_summary": _truncate(response_text, 600),
                "retrieval_score": None,
                "source_uri": "lightrag://query/response",
                "reference_id": "",
                "file_path": "",
                "citation_status": "complete" if standard_ref and clause_ref else "incomplete",
            }
        )
    return {
        "status": "success",
        "query": query,
        "summary": f"LightRAG 返回 {len(items)} 条标准参考。",
        "items": items[:max_results],
        "result_count": min(len(items), max_results),
        "truncated": len(items) > max_results,
        "trust_level": "semi_trusted_reference",
        "next_valid_actions": ["workspace.write_text", "psop.builder.submit_candidate"],
    }


def _record_standard_search_event(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
    result: dict[str, Any],
    started_at: float,
) -> None:
    items = result.get("items") if isinstance(result.get("items"), list) else []
    context.event_writer.record(
        "agent.tool.standard_search",
        {
            "tool_name": "psop.standard.search",
            "query_hash": _hash_text(str(arguments.get("query") or "")),
            "result_count": len(items),
            "standard_refs": [str(item.get("standard_ref") or "") for item in items if isinstance(item, dict) and item.get("standard_ref")][:8],
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "status": result.get("status"),
            "error_type": result.get("type", ""),
        },
    )


def _query_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/query"


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串。")
    return value.strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _snippet(chunks: list[Any], *, fallback: str) -> str:
    text = "\n\n".join(str(item).strip() for item in chunks if str(item).strip()).strip()
    if not text:
        text = fallback
    return _truncate(text, 1200)


def _title_from_reference(reference: dict[str, Any]) -> str:
    file_path = str(reference.get("file_path") or "").strip()
    if not file_path:
        return ""
    return Path(file_path).stem


def _first_match(pattern: re.Pattern[str], value: str) -> str:
    match = pattern.search(value)
    return match.group(0).strip() if match else ""


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + "\n...[truncated]"


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _error_result(error_type: str, message: str, *, retryable: bool) -> dict[str, Any]:
    return {
        "status": "error",
        "type": error_type,
        "message": message,
        "retryable": retryable,
        "next_valid_actions": ["continue_with_review_note"],
    }
