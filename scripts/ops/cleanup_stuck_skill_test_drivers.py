#!/usr/bin/env python3
"""Find and explicitly cancel legacy exhausted Timeline Driver jobs via REST."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE_URL = "http://127.0.0.1:8011/api/v1"
DEFAULT_REASON = "legacy timeline driver exhausted attempt budget"
DRIVER_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SCENARIO_TERMINAL_STATUSES = {"passed", "failed", "cancelled"}


class ApiRequestError(RuntimeError):
    pass


def request_json(
    api_base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{api_base_url.rstrip('/')}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied PSOP API endpoint
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiRequestError(f"{method} {request.full_url} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ApiRequestError(f"{method} {request.full_url} failed: {exc.reason}") from exc
    return json.loads(raw) if raw else None


def list_exhausted_driver_candidates(
    api_base_url: str,
    *,
    timeout: float = 30.0,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for status in ("pending", "retryable_failed"):
        offset = 0
        while True:
            query = urlencode(
                {
                    "status": status,
                    "job_type": "skill_test_timeline_driver",
                    "limit": page_size,
                    "offset": offset,
                }
            )
            jobs = request_json(api_base_url, f"runtime/jobs?{query}", timeout=timeout)
            if not isinstance(jobs, list):
                raise ApiRequestError("Runtime jobs endpoint returned a non-list response.")
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                attempt_no = _integer(job.get("attempt_no"))
                max_attempts = _integer(job.get("max_attempts"))
                scenario_run_id = str((job.get("payload") or {}).get("scenario_run_id") or "")
                if max_attempts <= 0 or attempt_no < max_attempts or not scenario_run_id:
                    continue
                scenario_run = request_json(
                    api_base_url,
                    f"skill-test-scenario-runs/{scenario_run_id}",
                    timeout=timeout,
                )
                if not isinstance(scenario_run, dict):
                    raise ApiRequestError(f"Scenario run `{scenario_run_id}` endpoint returned a non-object response.")
                if str(scenario_run.get("status") or "") in SCENARIO_TERMINAL_STATUSES:
                    continue
                if str(scenario_run.get("driver_status") or "") in DRIVER_TERMINAL_STATUSES:
                    continue
                candidates.append(_candidate_summary(job, scenario_run))
            if len(jobs) < page_size:
                break
            offset += page_size
    return candidates


def cancel_candidates(
    api_base_url: str,
    candidates: list[dict[str, Any]],
    *,
    reason: str = DEFAULT_REASON,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cancelled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for candidate in candidates:
        scenario_run_id = str(candidate["scenario_run_id"])
        try:
            result = request_json(
                api_base_url,
                f"skill-test-scenario-runs/{scenario_run_id}/cancel",
                method="POST",
                payload={"reason": reason},
                timeout=timeout,
            )
            cancelled.append(
                {
                    "job_id": candidate["job_id"],
                    "scenario_run_id": scenario_run_id,
                    "status": (result or {}).get("status"),
                    "driver_status": (result or {}).get("driver_status"),
                }
            )
        except (ApiRequestError, KeyError) as exc:
            failed.append(
                {
                    "job_id": candidate.get("job_id"),
                    "scenario_run_id": scenario_run_id,
                    "error": str(exc),
                }
            )
    return cancelled, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or cancel legacy Timeline Driver jobs stuck after exhausting their attempt budget."
    )
    parser.add_argument("--base-url", default=DEFAULT_API_BASE_URL, help="PSOP API base URL including /api/v1.")
    parser.add_argument("--apply", action="store_true", help="Cancel the matching scenario runs; default is dry-run.")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="Cancellation reason used with --apply.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        candidates = list_exhausted_driver_candidates(args.base_url, timeout=args.timeout)
    except ApiRequestError as exc:
        print(json.dumps({"mode": "apply" if args.apply else "dry-run", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    cancelled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if args.apply:
        cancelled, failed = cancel_candidates(
            args.base_url,
            candidates,
            reason=args.reason,
            timeout=args.timeout,
        )
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "candidate_count": len(candidates),
                "candidates": candidates,
                "cancelled_count": len(cancelled),
                "cancelled": cancelled,
                "failed_count": len(failed),
                "failed": failed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


def _candidate_summary(job: dict[str, Any], scenario_run: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("id"),
        "job_status": job.get("status"),
        "attempt_no": _integer(job.get("attempt_no")),
        "max_attempts": _integer(job.get("max_attempts")),
        "available_at": job.get("available_at"),
        "job_created_at": job.get("created_at"),
        "job_updated_at": job.get("updated_at"),
        "scenario_run_id": scenario_run.get("id"),
        "scenario_status": scenario_run.get("status"),
        "driver_status": scenario_run.get("driver_status"),
        "driver_cursor": _integer(scenario_run.get("driver_cursor")),
        "runtime_run_id": scenario_run.get("run_id"),
        "time_origin": scenario_run.get("time_origin"),
        "started_at": scenario_run.get("started_at"),
    }


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
