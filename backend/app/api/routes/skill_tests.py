from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_skill_test_service
from app.agents.schemas import AgentEventResponse
from app.pskills.exceptions import SkillsError, SkillValidationError
from app.runtime.schemas import InvocationResponse
from app.testing.activity import SkillTestRunActivityService
from app.testing.schemas import (
    CancelSkillTestScenarioRunRequest,
    DeleteSkillTestAssetResponse,
    ForkSkillDebugRequest,
    ForkSkillTestScenarioRequest,
    GenerateSkillTestScenariosRequest,
    GenerateSkillTestScenariosResponse,
    PSkillPublishGateResponse,
    RunPublishGateRequest,
    SkillTestAssetResponse,
    SkillTestScenarioCreateRequest,
    SkillTestScenarioResponse,
    SkillTestScenarioReviewResponse,
    SkillTestScenarioRunResponse,
    SkillTestScenarioUpdateRequest,
    SkillTestSuiteCreateRequest,
    SkillTestSuiteResponse,
    SkillTestSuiteRunResponse,
    StartSkillTestScenarioRunRequest,
)
from app.testing.service import SkillTestService


router = APIRouter(tags=["skill-tests"])
test_run_activity_ws_router = APIRouter(prefix="/ws", tags=["ws"])


@router.get("/testing/suites", response_model=list[SkillTestSuiteResponse])
def list_test_suites(
    pskill_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestSuiteResponse]:
    return service.list_suites(session, pskill_id=pskill_id, status=status)


@router.post("/testing/suites", response_model=SkillTestSuiteResponse, status_code=201)
def create_test_suite(
    payload: SkillTestSuiteCreateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestSuiteResponse:
    return service.create_suite(session, payload)


@router.post("/testing/suites/{suite_id}/scenarios", response_model=SkillTestScenarioResponse, status_code=201)
def create_test_suite_scenario(
    suite_id: str,
    payload: SkillTestScenarioCreateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.create_suite_scenario(session, suite_id, payload)


@router.post("/testing/suites/{suite_id}/run", response_model=SkillTestSuiteRunResponse, status_code=202)
def run_test_suite(
    suite_id: str,
    payload: StartSkillTestScenarioRunRequest | None = None,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestSuiteRunResponse:
    return service.run_suite(session, suite_id, payload or StartSkillTestScenarioRunRequest())


@router.get("/testing/runs/{test_run_id}", response_model=SkillTestScenarioRunResponse)
def get_testing_run(
    test_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.get_run(session, test_run_id)


@router.get("/testing/runs/{test_run_id}/events", response_model=list[AgentEventResponse])
def list_testing_run_events(
    test_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[AgentEventResponse]:
    return service.list_run_events(session, test_run_id)


@router.get("/pskills/{skill_id}/test-scenarios", response_model=list[SkillTestScenarioResponse])
def list_test_scenarios(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestScenarioResponse]:
    return service.list_scenarios(session, skill_id)


@router.post("/pskills/{skill_id}/test-scenarios", response_model=SkillTestScenarioResponse, status_code=201)
def create_test_scenario(
    skill_id: str,
    payload: SkillTestScenarioCreateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.create_scenario(session, skill_id, payload)


@router.post(
    "/testing/pskills/{skill_id}/generate-scenarios",
    response_model=GenerateSkillTestScenariosResponse,
    status_code=201,
)
def generate_test_scenarios(
    skill_id: str,
    payload: GenerateSkillTestScenariosRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> GenerateSkillTestScenariosResponse:
    return service.generate_scenarios(session, skill_id, payload)


@router.get("/pskills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def get_test_scenario(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.get_scenario(session, skill_id, scenario_id)


@router.patch("/pskills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def update_test_scenario(
    skill_id: str,
    scenario_id: str,
    payload: SkillTestScenarioUpdateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.update_scenario(session, skill_id, scenario_id, payload)


@router.delete("/pskills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def delete_test_scenario(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.delete_scenario(session, skill_id, scenario_id)


@router.post("/pskills/{skill_id}/test-scenarios/{scenario_id}/assets", response_model=SkillTestAssetResponse, status_code=201)
async def upload_test_scenario_asset(
    skill_id: str,
    scenario_id: str,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    lane_id: str = Form(default="input.file"),
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestAssetResponse:
    content = await file.read()
    return service.upload_asset(
        session,
        skill_id,
        scenario_id,
        filename=file.filename or "upload.bin",
        content=content,
        mime_type=file.content_type or "application/octet-stream",
        name=name,
        description=description,
        lane_id=lane_id,
    )


@router.get("/pskills/{skill_id}/test-scenarios/{scenario_id}/assets", response_model=list[SkillTestAssetResponse])
def list_test_scenario_assets(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestAssetResponse]:
    return service.list_assets(session, skill_id, scenario_id)


@router.get("/pskills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}/content")
def get_test_scenario_asset_content(
    skill_id: str,
    scenario_id: str,
    asset_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> Response:
    asset_content = service.get_asset_content(session, skill_id, scenario_id, asset_id)
    encoded_filename = quote(asset_content.filename)
    return Response(
        content=asset_content.content,
        media_type=asset_content.mime_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"},
    )


@router.delete(
    "/pskills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}",
    response_model=DeleteSkillTestAssetResponse,
)
def delete_test_scenario_asset(
    skill_id: str,
    scenario_id: str,
    asset_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> DeleteSkillTestAssetResponse:
    return service.delete_asset(session, skill_id, scenario_id, asset_id)


@router.post("/pskills/{skill_id}/test-scenarios/{scenario_id}/runs", response_model=SkillTestScenarioRunResponse, status_code=202)
def start_test_scenario_run(
    skill_id: str,
    scenario_id: str,
    payload: StartSkillTestScenarioRunRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.start_run(session, skill_id, scenario_id, payload)


@router.get("/pskills/{skill_id}/test-scenarios/{scenario_id}/runs", response_model=list[SkillTestScenarioRunResponse])
def list_test_scenario_runs(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestScenarioRunResponse]:
    return service.list_runs(session, skill_id, scenario_id)


@router.get("/skill-test-scenario-runs/{scenario_run_id}", response_model=SkillTestScenarioRunResponse)
def get_test_scenario_run(
    scenario_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.get_run(session, scenario_run_id)


@router.post("/skill-test-scenario-runs/{scenario_run_id}/cancel", response_model=SkillTestScenarioRunResponse)
def cancel_test_scenario_run(
    scenario_run_id: str,
    payload: CancelSkillTestScenarioRunRequest | None = None,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.cancel_run(session, scenario_run_id, reason=(payload.reason if payload else "cancelled by user"))


@router.get("/skill-test-scenario-runs/{scenario_run_id}/review", response_model=SkillTestScenarioReviewResponse)
def get_test_scenario_run_review(
    scenario_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioReviewResponse:
    return service.get_review(session, scenario_run_id)


@router.post("/skill-test-scenario-runs/{scenario_run_id}/evaluate", response_model=SkillTestScenarioRunResponse)
def evaluate_test_scenario_run(
    scenario_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.evaluate_run(session, scenario_run_id)


@router.post("/skill-test-scenario-runs/{scenario_run_id}/fork-scenario", response_model=SkillTestScenarioResponse, status_code=201)
def fork_test_scenario(
    scenario_run_id: str,
    payload: ForkSkillTestScenarioRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.fork_scenario(session, scenario_run_id, payload)


@router.post("/skill-test-scenario-runs/{scenario_run_id}/fork-debug", response_model=InvocationResponse, status_code=201)
def fork_test_scenario_debug(
    scenario_run_id: str,
    payload: ForkSkillDebugRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> InvocationResponse:
    return service.fork_debug(session, scenario_run_id, payload)


@router.post("/testing/publish-gate/run", response_model=PSkillPublishGateResponse, status_code=201)
def run_publish_gate(
    payload: RunPublishGateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> PSkillPublishGateResponse:
    if not payload.pskill_id:
        raise SkillValidationError("publish gate 需要 pskill_id。")
    return service.run_publish_gate(session, payload.pskill_id, payload)


@test_run_activity_ws_router.websocket("/test-runs/{test_run_id}")
async def test_run_activity_websocket(websocket: WebSocket, test_run_id: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "event_type": "ws.connected",
            "test_run_id": test_run_id,
            "occurred_at": None,
            "payload": {"message": "connected"},
        }
    )
    service = SkillTestRunActivityService(testing_service=_build_skill_test_service(websocket))
    last_payload = ""
    try:
        while True:
            with websocket.app.state.db_manager.session() as session:
                snapshot = service.build_snapshot(session, test_run_id)
            encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            if encoded != last_payload:
                await websocket.send_json(
                    {
                        "event_type": "test_run.activity.snapshot",
                        "test_run_id": test_run_id,
                        "occurred_at": snapshot["test_run"]["updated_at"],
                        "payload": snapshot,
                    }
                )
                last_payload = encoded
            await asyncio.sleep(1)
    except SkillsError as exc:
        await websocket.send_json(
            {
                "event_type": "test_run.activity.error",
                "test_run_id": test_run_id,
                "occurred_at": None,
                "payload": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        )
        await websocket.close(code=1008)
    except (RuntimeError, WebSocketDisconnect):
        return


def _build_skill_test_service(websocket: WebSocket) -> SkillTestService:
    return SkillTestService(
        settings=websocket.app.state.settings,
        inference_gateway=websocket.app.state.inference_gateway,
        object_store=websocket.app.state.object_store,
    )
