from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_skills_service
from app.domain.skills.exceptions import SkillValidationError
from app.domain.skills.schemas import (
    CreateSkillRepositoryFileRequest,
    CreateSkillRepositoryFolderRequest,
    CreateSkillRequest,
    DeleteSkillRequest,
    DeleteSkillRawMaterialResponse,
    GenerateSkillDraftRequest,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillRepositoryFileRequest,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    SkillPublishRecordResponse,
    SkillRawMaterialDetailResponse,
    SkillRawMaterialGenerationResponse,
    SkillRawMaterialResponse,
    SkillRepositoryFileResponse,
    SkillRepositoryTreeResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    UpdateSkillRequest,
)
from app.domain.skills.service import SkillsService


router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillSummaryResponse])
def list_skills(
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillSummaryResponse]:
    return service.list_skills(session, search=search, status=status)


@router.post("", response_model=SkillDetailResponse, status_code=201)
def create_skill(
    payload: CreateSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.create_skill(session, payload)


@router.get("/{skill_id}", response_model=SkillDetailResponse)
def get_skill_detail(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.get_skill_detail(session, skill_id)


@router.patch("/{skill_id}", response_model=SkillDetailResponse)
def update_skill_metadata(
    skill_id: str,
    payload: UpdateSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.update_skill_metadata(session, skill_id=skill_id, payload=payload)


@router.delete("/{skill_id}", response_model=SkillSummaryResponse)
def delete_skill(
    skill_id: str,
    payload: DeleteSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillSummaryResponse:
    return service.delete_skill(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/source", response_model=SkillSourceResponse)
def get_skill_source(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillSourceResponse:
    return service.get_skill_source(session, skill_id)


@router.put("/{skill_id}/source", response_model=SkillSourceResponse)
def save_skill_source(
    skill_id: str,
    payload: SaveSkillSourceRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillSourceResponse:
    return service.save_skill_source(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/repository/tree", response_model=SkillRepositoryTreeResponse)
def list_repository_tree(
    skill_id: str,
    path: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRepositoryTreeResponse:
    return service.list_repository_tree(session, skill_id=skill_id, path=path)


@router.get("/{skill_id}/repository/files", response_model=SkillRepositoryFileResponse)
def get_repository_file(
    skill_id: str,
    path: str = Query(min_length=1),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRepositoryFileResponse:
    return service.get_repository_file(session, skill_id=skill_id, path=path)


@router.put("/{skill_id}/repository/files", response_model=SkillRepositoryFileResponse)
def save_repository_file(
    skill_id: str,
    payload: SaveSkillRepositoryFileRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRepositoryFileResponse:
    return service.save_repository_file(session, skill_id=skill_id, payload=payload)


@router.post("/{skill_id}/repository/files", response_model=SkillRepositoryFileResponse, status_code=201)
def create_repository_file(
    skill_id: str,
    payload: CreateSkillRepositoryFileRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRepositoryFileResponse:
    return service.create_repository_file(session, skill_id=skill_id, payload=payload)


@router.post("/{skill_id}/repository/folders", response_model=SkillRepositoryFileResponse, status_code=201)
def create_repository_folder(
    skill_id: str,
    payload: CreateSkillRepositoryFolderRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRepositoryFileResponse:
    return service.create_repository_folder(session, skill_id=skill_id, payload=payload)


@router.post("/{skill_id}/publish", response_model=PublishSkillResponse, status_code=status.HTTP_202_ACCEPTED)
def publish_skill(
    skill_id: str,
    payload: PublishSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> PublishSkillResponse:
    return service.publish_skill(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/publishes", response_model=list[SkillPublishRecordResponse])
def list_publish_records(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillPublishRecordResponse]:
    return service.list_publish_records(session, skill_id=skill_id)


@router.post("/{skill_id}/raw-materials", response_model=SkillRawMaterialDetailResponse, status_code=201)
async def create_raw_material(
    skill_id: str,
    file: UploadFile | None = File(default=None),
    source_url: str | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    material_kind: str | None = Form(default=None),
    source_note: str | None = Form(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialDetailResponse:
    has_file = file is not None
    has_url = bool(source_url and source_url.strip())
    if has_file == has_url:
        raise SkillValidationError("请上传文件或填写参考 URL，且二者只能选择一个。")

    if file is not None:
        content = await file.read()
        return service.upload_raw_material(
            session,
            skill_id=skill_id,
            filename=file.filename or "raw-material",
            content=content,
            mime_type=file.content_type or "application/octet-stream",
            name=name,
            description=description,
            material_kind=material_kind,
            source_note=source_note or "",
        )

    return service.create_raw_material_from_url(
        session,
        skill_id=skill_id,
        source_url=source_url or "",
        name=name,
        description=description,
        material_kind=material_kind,
    )


@router.get("/{skill_id}/raw-materials", response_model=list[SkillRawMaterialResponse])
def list_raw_materials(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillRawMaterialResponse]:
    return service.list_raw_materials(session, skill_id=skill_id)


@router.post(
    "/{skill_id}/raw-materials/generate-skill-draft",
    response_model=SkillRawMaterialGenerationResponse,
)
def generate_skill_draft_from_raw_materials(
    skill_id: str,
    payload: GenerateSkillDraftRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialGenerationResponse:
    return service.generate_skill_draft_from_raw_materials(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/raw-materials/{material_id}", response_model=SkillRawMaterialDetailResponse)
def get_raw_material(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialDetailResponse:
    return service.get_raw_material(session, skill_id=skill_id, material_id=material_id)


@router.get("/{skill_id}/raw-materials/{material_id}/content")
def get_raw_material_content(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> Response:
    material_content = service.get_raw_material_content(session, skill_id=skill_id, material_id=material_id)
    encoded_filename = quote(material_content.filename)
    return Response(
        content=material_content.content,
        media_type=material_content.mime_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"},
    )


@router.delete("/{skill_id}/raw-materials/{material_id}", response_model=DeleteSkillRawMaterialResponse)
def delete_raw_material(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> DeleteSkillRawMaterialResponse:
    return service.delete_raw_material(session, skill_id=skill_id, material_id=material_id)
