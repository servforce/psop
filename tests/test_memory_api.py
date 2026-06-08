from __future__ import annotations

from tests.test_skills_api import create_test_client


def test_memory_api_lists_searches_and_reviews_agent_memory_candidates() -> None:
    client, _, _ = create_test_client()

    with client:
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.evaluator",
                "owner_type": "run_evaluation",
                "owner_id": "evaluation-memory-1",
                "input_payload": {
                    "expected_output": {
                        "schema": "RunEvaluationResult",
                        "summary": "Evaluation produced an episodic memory candidate.",
                        "memory_candidates": [
                            {
                                "namespace": "evaluation",
                                "memory_type": "episodic",
                                "title": "Runtime provider failure replay pattern",
                                "content": "Runtime provider failures should be replayed with run_trace evidence before governance changes.",
                                "confidence": 82,
                                "source_refs": [{"kind": "run_trace", "id": "trace-1"}],
                                "tags": ["runtime", "failure"],
                                "metadata": {"quality_score": 40},
                            }
                        ],
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        run_once_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        agent_run_memory_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/memory-entries")
        memory_list_response = client.get(
            "/api/v1/memory",
            params={"namespace": "evaluation", "memory_type": "episodic", "status": "pending_review"},
        )
        entry = memory_list_response.json()[0]
        search_pending_response = client.post(
            "/api/v1/memory/search",
            json={"query": "provider failure", "status": "pending_review", "limit": 10},
        )
        patch_response = client.patch(
            f"/api/v1/memory/{entry['id']}",
            json={
                "status": "active",
                "content": "Runtime provider failures require replay and OTel correlation before governance changes.",
                "confidence": 90,
                "tags": ["runtime", "replay"],
            },
        )
        search_active_response = client.post(
            "/api/v1/memory/search",
            json={"query": "OTel correlation", "namespace": "evaluation"},
        )

    assert run_response.status_code == 201
    assert run_once_response.status_code == 200
    assert run_once_response.json()["status"] == "succeeded"
    assert run_once_response.json()["output_payload"]["memory_candidates"][0]["memory_type"] == "episodic"
    assert "agent.memory_candidates.written" in [item["event_type"] for item in events_response.json()]
    assert agent_run_memory_response.status_code == 200
    assert [item["created_by_agent_run_id"] for item in agent_run_memory_response.json()] == [agent_run_id]

    assert memory_list_response.status_code == 200
    assert entry["namespace"] == "evaluation"
    assert entry["memory_type"] == "episodic"
    assert entry["status"] == "pending_review"
    assert entry["agent_key"] == "pskill.evaluator"
    assert entry["created_by_agent_run_id"] == agent_run_id
    assert entry["source_refs"] == [{"kind": "run_trace", "id": "trace-1"}]

    assert search_pending_response.status_code == 200
    assert search_pending_response.json()[0]["id"] == entry["id"]
    assert patch_response.status_code == 200
    assert patch_response.json()["status"] == "active"
    assert patch_response.json()["confidence"] == 90
    assert patch_response.json()["reviewed_at"]
    assert patch_response.json()["content"].startswith("Runtime provider failures require replay")
    assert search_active_response.status_code == 200
    assert search_active_response.json()[0]["id"] == entry["id"]


def test_memory_api_rejects_invalid_memory_type_filter() -> None:
    client, _, _ = create_test_client()

    with client:
        response = client.get("/api/v1/memory", params={"memory_type": "runtime_fact"})

    assert response.status_code == 422
    assert response.json()["code"] == "skill_validation_error"
