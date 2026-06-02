from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
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
    SkillRawMaterialAnalysisResponse,
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
    is_published: bool | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillSummaryResponse]:
    return service.list_skills(session, search=search, status=status, is_published=is_published)


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
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    material_kind: str | None = Form(default=None),
    source_note: str | None = Form(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialDetailResponse:
    if file is None:
        raise SkillValidationError("请上传素材文件。")

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


@router.post(
    "/{skill_id}/raw-materials/{material_id}/analyze",
    response_model=SkillRawMaterialAnalysisResponse,
)
def analyze_raw_material(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialAnalysisResponse:
    return service.analyze_raw_material(session, skill_id=skill_id, material_id=material_id)


@router.get(
    "/{skill_id}/raw-materials/{material_id}/analysis",
    response_model=SkillRawMaterialAnalysisResponse,
)
def get_raw_material_analysis(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillRawMaterialAnalysisResponse:
    return service.get_raw_material_analysis(session, skill_id=skill_id, material_id=material_id)


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
    request: Request,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> Response:
    material_content = service.get_raw_material_content(session, skill_id=skill_id, material_id=material_id)
    return _inline_content_response(
        content=material_content.content,
        mime_type=material_content.mime_type,
        filename=material_content.filename,
        range_header=request.headers.get("range"),
    )


@router.get("/{skill_id}/raw-materials/{material_id}/derived-assets/{asset_id}/content")
def get_raw_material_derived_asset_content(
    skill_id: str,
    material_id: str,
    asset_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> Response:
    asset_content = service.get_raw_material_derived_asset_content(
        session,
        skill_id=skill_id,
        material_id=material_id,
        asset_id=asset_id,
    )
    return _inline_content_response(
        content=asset_content.content,
        mime_type=asset_content.mime_type,
        filename=asset_content.filename,
        range_header=None,
    )


def _inline_content_response(
    *,
    content: bytes,
    mime_type: str,
    filename: str,
    range_header: str | None,
) -> Response:
    encoded_filename = quote(filename)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
    }
    size = len(content)
    if not range_header:
        headers["Content-Length"] = str(size)
        return Response(content=content, media_type=mime_type, headers=headers)

    byte_range = _parse_single_byte_range(range_header, size)
    if byte_range is None:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )

    start, end = byte_range
    partial = content[start : end + 1]
    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(len(partial)),
        }
    )
    return Response(
        content=partial,
        media_type=mime_type,
        headers=headers,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
    )


def _parse_single_byte_range(range_header: str, size: int) -> tuple[int, int] | None:
    if size <= 0:
        return None
    unit, separator, spec = range_header.partition("=")
    if separator != "=" or unit.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if not spec or "," in spec or "-" not in spec:
        return None
    start_text, end_text = [part.strip() for part in spec.split("-", 1)]
    if not start_text:
        if not end_text.isdigit():
            return None
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        return max(size - suffix_length, 0), size - 1
    if not start_text.isdigit() or (end_text and not end_text.isdigit()):
        return None
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


@router.delete("/{skill_id}/raw-materials/{material_id}", response_model=DeleteSkillRawMaterialResponse)
def delete_raw_material(
    skill_id: str,
    material_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> DeleteSkillRawMaterialResponse:
    return service.delete_raw_material(session, skill_id=skill_id, material_id=material_id)
