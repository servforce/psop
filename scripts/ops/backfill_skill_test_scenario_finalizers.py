#!/usr/bin/env python3
"""Find completed Scenario Drivers that still need a Scenario Finalizer job."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE_URL = "http://127.0.0.1:8011/api/v1"
SCENARIO_TERMINAL_STATUSES = {"passed", "failed", "cancelled"}
ACTIVE_FINALIZER_STATUSES = {"pending", "running", "retryable_failed"}


class ApiRequestError(RuntimeError):
    pass


def request_json(
    api_base_url: str,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 30.0,
) -> Any:
    request = Request(
        f"{api_base_url.rstrip('/')}/{path.lstrip('/')}",
        method=method,
        headers={"Content-Type": "application/json"},
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


def list_jobs(
    api_base_url: str,
    *,
    job_type: str,
    status: str | None = None,
    timeout: float = 30.0,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    offset = 0
    while True:
        query_values: dict[str, Any] = {
            "job_type": job_type,
            "limit": page_size,
            "offset": offset,
        }
        if status:
            query_values["status"] = status
        page = request_json(api_base_url, f"runtime/jobs?{urlencode(query_values)}", timeout=timeout)
        if not isinstance(page, list):
            raise ApiRequestError("Runtime jobs endpoint returned a non-list response.")
        jobs.extend(item for item in page if isinstance(item, dict))
        if len(page) < page_size:
            return jobs
        offset += page_size


def list_backfill_candidates(
    api_base_url: str,
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    finalizers_by_scenario = {
        str((job.get("payload") or {}).get("scenario_run_id") or ""): job
        for job in list_jobs(
            api_base_url,
            job_type="skill_test_scenario_finalizer",
            timeout=timeout,
        )
        if str((job.get("payload") or {}).get("scenario_run_id") or "")
    }
    candidates: list[dict[str, Any]] = []
    seen_scenario_ids: set[str] = set()
    for driver in list_jobs(
        api_base_url,
        job_type="skill_test_timeline_driver",
        status="succeeded",
        timeout=timeout,
    ):
        scenario_run_id = str((driver.get("payload") or {}).get("scenario_run_id") or "")
        if not scenario_run_id or scenario_run_id in seen_scenario_ids:
            continue
        seen_scenario_ids.add(scenario_run_id)
        scenario_run = request_json(
            api_base_url,
            f"skill-test-scenario-runs/{scenario_run_id}",
            timeout=timeout,
        )
        if not isinstance(scenario_run, dict):
            raise ApiRequestError(f"Scenario run `{scenario_run_id}` endpoint returned a non-object response.")
        if str(scenario_run.get("status") or "") in SCENARIO_TERMINAL_STATUSES:
            continue
        if str(scenario_run.get("driver_status") or "") != "completed":
            continue
        finalizer = finalizers_by_scenario.get(scenario_run_id)
        if finalizer and str(finalizer.get("status") or "") in ACTIVE_FINALIZER_STATUSES:
            continue
        candidates.append(
            {
                "scenario_run_id": scenario_run_id,
                "scenario_status": scenario_run.get("status"),
                "runtime_run_id": scenario_run.get("run_id"),
                "pending_expectations": _integer((scenario_run.get("result_summary") or {}).get("pending")),
                "driver_job_id": driver.get("id"),
                "finalizer_job_id": finalizer.get("id") if finalizer else None,
                "finalizer_job_status": finalizer.get("status") if finalizer else None,
            }
        )
    return candidates


def enqueue_candidates(
    api_base_url: str,
    candidates: list[dict[str, Any]],
    *,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    enqueued: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for candidate in candidates:
        scenario_run_id = str(candidate["scenario_run_id"])
        try:
            result = request_json(
                api_base_url,
                f"skill-test-scenario-runs/{scenario_run_id}/evaluate",
                method="POST",
                timeout=timeout,
            )
            enqueued.append(
                {
                    "scenario_run_id": scenario_run_id,
                    "status": (result or {}).get("status"),
                }
            )
        except (ApiRequestError, KeyError) as exc:
            failed.append({"scenario_run_id": scenario_run_id, "error": str(exc)})
    return enqueued, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or enqueue missing Scenario Finalizer jobs for completed timeline drivers."
    )
    parser.add_argument("--base-url", default=DEFAULT_API_BASE_URL, help="PSOP API base URL including /api/v1.")
    parser.add_argument("--apply", action="store_true", help="Enqueue the matching Scenario Finalizers; default is dry-run.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        candidates = list_backfill_candidates(args.base_url, timeout=args.timeout)
    except ApiRequestError as exc:
        print(json.dumps({"mode": "apply" if args.apply else "dry-run", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    enqueued: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if args.apply:
        enqueued, failed = enqueue_candidates(args.base_url, candidates, timeout=args.timeout)
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "candidate_count": len(candidates),
                "candidates": candidates,
                "enqueued_count": len(enqueued),
                "enqueued": enqueued,
                "failed_count": len(failed),
                "failed": failed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
