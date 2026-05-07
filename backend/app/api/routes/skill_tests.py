from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_skill_test_service
from app.domain.skill_tests.schemas import (
    DeleteSkillTestDataResponse,
    SendSkillTestDataRequest,
    SendSkillTestDataResponse,
    SkillTestCaseCreateRequest,
    SkillTestCaseResponse,
    SkillTestCaseUpdateRequest,
    SkillTestDataObjectResponse,
    SkillTestRunResponse,
    StartSkillTestRunRequest,
)
from app.domain.skill_tests.service import SkillTestService


router = APIRouter(tags=["skill-tests"])


@router.get("/skills/{skill_id}/test-cases", response_model=list[SkillTestCaseResponse])
def list_test_cases(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestCaseResponse]:
    return service.list_cases(session, skill_id)


@router.post("/skills/{skill_id}/test-cases", response_model=SkillTestCaseResponse, status_code=201)
def create_test_case(
    skill_id: str,
    payload: SkillTestCaseCreateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestCaseResponse:
    return service.create_case(session, skill_id, payload)


@router.get("/skills/{skill_id}/test-cases/{case_id}", response_model=SkillTestCaseResponse)
def get_test_case(
    skill_id: str,
    case_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestCaseResponse:
    return service.get_case(session, skill_id, case_id)


@router.patch("/skills/{skill_id}/test-cases/{case_id}", response_model=SkillTestCaseResponse)
def update_test_case(
    skill_id: str,
    case_id: str,
    payload: SkillTestCaseUpdateRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestCaseResponse:
    return service.update_case(session, skill_id, case_id, payload)


@router.delete("/skills/{skill_id}/test-cases/{case_id}", response_model=SkillTestCaseResponse)
def delete_test_case(
    skill_id: str,
    case_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestCaseResponse:
    return service.delete_case(session, skill_id, case_id)


@router.get("/skills/{skill_id}/test-cases/{case_id}/data", response_model=list[SkillTestDataObjectResponse])
def list_test_data(
    skill_id: str,
    case_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestDataObjectResponse]:
    return service.list_data_objects(session, skill_id, case_id)


@router.post("/skills/{skill_id}/test-cases/{case_id}/data", response_model=SkillTestDataObjectResponse, status_code=201)
async def upload_test_data(
    skill_id: str,
    case_id: str,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    role: str = Form(default="input"),
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestDataObjectResponse:
    content = await file.read()
    return service.upload_data_object(
        session,
        skill_id,
        case_id,
        filename=file.filename or "upload.bin",
        content=content,
        mime_type=file.content_type or "application/octet-stream",
        name=name,
        description=description,
        role=role,
    )


@router.delete("/skills/{skill_id}/test-cases/{case_id}/data/{data_id}", response_model=DeleteSkillTestDataResponse)
def delete_test_data(
    skill_id: str,
    case_id: str,
    data_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> DeleteSkillTestDataResponse:
    return service.delete_data_object(session, skill_id, case_id, data_id)


@router.post("/skills/{skill_id}/test-cases/{case_id}/runs", response_model=SkillTestRunResponse, status_code=202)
def start_test_run(
    skill_id: str,
    case_id: str,
    payload: StartSkillTestRunRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestRunResponse:
    return service.start_run(session, skill_id, case_id, payload)


@router.get("/skills/{skill_id}/test-cases/{case_id}/runs", response_model=list[SkillTestRunResponse])
def list_test_runs(
    skill_id: str,
    case_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> list[SkillTestRunResponse]:
    return service.list_runs(session, skill_id, case_id)


@router.get("/skill-test-runs/{test_run_id}", response_model=SkillTestRunResponse)
def get_test_run(
    test_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestRunResponse:
    return service.get_run(session, test_run_id)


@router.post("/skill-test-runs/{test_run_id}/send-data", response_model=SendSkillTestDataResponse, status_code=202)
def send_test_data(
    test_run_id: str,
    payload: SendSkillTestDataRequest,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SendSkillTestDataResponse:
    return service.send_data(session, test_run_id, payload)


@router.post("/skill-test-runs/{test_run_id}/evaluate", response_model=SkillTestRunResponse)
def evaluate_test_run(
    test_run_id: str,
    session: Session = Depends(get_db_session),
    service: SkillTestService = Depends(get_skill_test_service),
) -> SkillTestRunResponse:
    return service.evaluate_run(session, test_run_id)
