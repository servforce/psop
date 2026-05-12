from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_skill_test_service
from app.domain.runtime.schemas import InvocationResponse
from app.domain.skill_tests.schemas import (
    DeleteSkillTestAssetResponse,
    ForkSkillDebugRequest,
    ForkSkillTestScenarioRequest,
    SkillTestAssetResponse,
    SkillTestScenarioCreateRequest,
    SkillTestScenarioResponse,
    SkillTestScenarioReviewResponse,
    SkillTestScenarioRunResponse,
    SkillTestScenarioUpdateRequest,
    StartSkillTestScenarioRunRequest,
)
from app.domain.skill_tests.service import SkillTestService


router = APIRouter(tags=["skill-tests"])


@router.get("/skills/{skill_id}/test-scenarios", response_model=list[SkillTestScenarioResponse])
def list_test_scenarios(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestScenarioResponse]:
    return service.list_scenarios(session, skill_id)


@router.post("/skills/{skill_id}/test-scenarios", response_model=SkillTestScenarioResponse, status_code=201)
def create_test_scenario(
    skill_id: str,
    payload: SkillTestScenarioCreateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.create_scenario(session, skill_id, payload)


@router.get("/skills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def get_test_scenario(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.get_scenario(session, skill_id, scenario_id)


@router.patch("/skills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def update_test_scenario(
    skill_id: str,
    scenario_id: str,
    payload: SkillTestScenarioUpdateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.update_scenario(session, skill_id, scenario_id, payload)


@router.delete("/skills/{skill_id}/test-scenarios/{scenario_id}", response_model=SkillTestScenarioResponse)
def delete_test_scenario(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioResponse:
    return service.delete_scenario(session, skill_id, scenario_id)


@router.post("/skills/{skill_id}/test-scenarios/{scenario_id}/assets", response_model=SkillTestAssetResponse, status_code=201)
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


@router.get("/skills/{skill_id}/test-scenarios/{scenario_id}/assets", response_model=list[SkillTestAssetResponse])
def list_test_scenario_assets(
    skill_id: str,
    scenario_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestAssetResponse]:
    return service.list_assets(session, skill_id, scenario_id)


@router.delete(
    "/skills/{skill_id}/test-scenarios/{scenario_id}/assets/{asset_id}",
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


@router.post("/skills/{skill_id}/test-scenarios/{scenario_id}/runs", response_model=SkillTestScenarioRunResponse, status_code=202)
def start_test_scenario_run(
    skill_id: str,
    scenario_id: str,
    payload: StartSkillTestScenarioRunRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestScenarioRunResponse:
    return service.start_run(session, skill_id, scenario_id, payload)


@router.get("/skills/{skill_id}/test-scenarios/{scenario_id}/runs", response_model=list[SkillTestScenarioRunResponse])
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
