from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.domain.skills.exceptions import SkillValidationError, SkillsGatewayError
from app.gateway.inference import LlmAttachment, LlmCompletion, LlmInferenceGateway
from app.infra.object_store import ObjectStoreService, StoredObject


REQUIRED_GENERATED_FILES = [
    "README.md",
    "SKILL.md",
    "prompts/system.md",
    "references/README.md",
    "examples/input.md",
    "examples/expected-output.md",
    "tests/checklist.md",
]


@dataclass(frozen=True, slots=True)
class MaterialContent:
    filename: str
    content: bytes
    mime_type: str
    source_note: str = ""


@dataclass(frozen=True, slots=True)
class MaterialExtraction:
    status: str
    parse_summary: str
    extracted_text: str = ""
    processing_metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class StoredRawMaterial:
    stored: StoredObject
    artifact_payload: dict[str, Any]
    extraction: MaterialExtraction


@dataclass(frozen=True, slots=True)
class GeneratedSkillDraft:
    files: dict[str, str]
    generation_reason: str
    review_notes: list[str]
    material_usage: list[dict[str, Any]]
    directory_tree: str
    raw_parsed: dict[str, Any]


class RawMaterialProcessor:
    def __init__(
        self,
        *,
        settings: Settings,
        inference_gateway: LlmInferenceGateway,
        object_store: ObjectStoreService,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.object_store = object_store

    def fetch_url(self, source_url: str) -> MaterialContent:
        parsed = urlparse(source_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SkillValidationError("参考网址必须是 http 或 https URL。", details={"source_url": source_url})

        max_bytes = self._max_upload_bytes()
        timeout = httpx.Timeout(self._url_timeout_seconds(), connect=min(10.0, self._url_timeout_seconds()))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(source_url)
                response.raise_for_status()
                content = response.content
        except httpx.HTTPError as exc:
            raise SkillsGatewayError(
                "参考网址抓取失败。",
                details={"source_url": source_url, "error": str(exc)},
            ) from exc

        if len(content) > max_bytes:
            raise SkillValidationError(
                "参考网址内容超过大小限制。",
                details={"source_url": source_url, "max_bytes": max_bytes, "size_bytes": len(content)},
            )
        content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip()
        filename = _filename_from_url(source_url, content_type)
        return MaterialContent(
            filename=filename,
            content=content,
            mime_type=content_type or "application/octet-stream",
            source_note=source_url,
        )

    def store_and_extract(
        self,
        *,
        skill_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str,
        description: str,
        material_kind: str,
        source_note: str,
    ) -> StoredRawMaterial:
        self._validate_upload(filename=filename, content=content, mime_type=mime_type)
        safe_filename = _safe_filename(filename)
        object_key = "/".join(["skill-raw-materials", skill_id, f"{uuid.uuid4()}-{safe_filename}"])
        stored = self.object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=mime_type,
            metadata={
                "skill_id": skill_id,
                "filename": safe_filename,
                "material_kind": material_kind,
                "source": "skill_raw_material",
            },
        )
        extraction = self.extract(
            filename=safe_filename,
            content=content,
            mime_type=mime_type,
            name=name,
            description=description,
            material_kind=material_kind,
            source_note=source_note,
        )
        artifact_payload = {
            "kind": "skill_raw_material",
            "filename": safe_filename,
            "name": name,
            "description": description,
            "material_kind": material_kind,
            "source_note": source_note,
            "metadata": stored.metadata,
        }
        return StoredRawMaterial(stored=stored, artifact_payload=artifact_payload, extraction=extraction)

    def extract(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str,
        description: str,
        material_kind: str,
        source_note: str,
    ) -> MaterialExtraction:
        try:
            if _is_textual(filename, mime_type):
                extracted = _decode_text(content)
                if _is_html(filename, mime_type):
                    extracted = _html_to_text(extracted)
                return _text_extraction(extracted, source="local_text")
            if _is_pdf(filename, mime_type):
                return _text_extraction(_extract_pdf_text(content), source="local_pdf")
            if mime_type.startswith("image/"):
                return self._multimodal_extraction(
                    filename=filename,
                    content=content,
                    mime_type=mime_type,
                    prompt_context={
                        "name": name,
                        "description": description,
                        "material_kind": material_kind,
                        "source_note": source_note,
                    },
                )
            if mime_type.startswith("audio/"):
                return self._multimodal_extraction(
                    filename=filename,
                    content=content,
                    mime_type=mime_type,
                    prompt_context={
                        "name": name,
                        "description": description,
                        "material_kind": material_kind,
                        "source_note": source_note,
                    },
                )
            if mime_type.startswith("video/"):
                frame = _extract_video_frame(content, filename)
                return self._multimodal_extraction(
                    filename=f"{Path(filename).stem or 'video'}-frame.jpg",
                    content=frame,
                    mime_type="image/jpeg",
                    prompt_context={
                        "name": name,
                        "description": description,
                        "material_kind": material_kind,
                        "source_note": source_note,
                        "original_filename": filename,
                        "original_mime_type": mime_type,
                    },
                )
            return MaterialExtraction(
                status="ready",
                parse_summary="素材已保存。当前类型没有可提取文本，生成时将使用文件元数据和用户描述。",
                processing_metadata={"processor": "metadata_only"},
            )
        except Exception as exc:
            return MaterialExtraction(
                status="failed",
                parse_summary="素材已保存，但解析失败。",
                processing_metadata={"processor": "failed", "error_type": exc.__class__.__name__},
                error_message=str(exc),
            )

    def _multimodal_extraction(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        prompt_context: dict[str, Any],
    ) -> MaterialExtraction:
        if not hasattr(self.inference_gateway, "complete_multimodal"):
            raise SkillsGatewayError("当前 LLM Inference Gateway 不支持多模态素材解析。")
        system_prompt = (
            "你是 PSOP 原始素材解析器。请分析用户上传的素材，提取对创建现实任务 Skill 有用的信息。"
            "必须输出 JSON：{\"summary\":\"...\",\"extracted_text\":\"...\",\"signals\":[\"...\"]}。"
        )
        user_prompt = json.dumps(
            {
                "task": "extract_raw_material_for_skill_creation",
                "filename": filename,
                "mime_type": mime_type,
                "context": prompt_context,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        completion: LlmCompletion = self.inference_gateway.complete_multimodal(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            attachments=[
                LlmAttachment(
                    filename=filename,
                    media_type=mime_type,
                    content_base64=base64.b64encode(content).decode("ascii"),
                )
            ],
            route_key="default",
        )
        parsed = _parse_json_object(completion.content)
        summary = str(parsed.get("summary") or "多模态素材解析完成。").strip()
        extracted = str(parsed.get("extracted_text") or "").strip()
        return MaterialExtraction(
            status="ready",
            parse_summary=_truncate(summary, 2000),
            extracted_text=_truncate(extracted, self._extract_text_budget_chars()),
            processing_metadata={
                "processor": "llm_multimodal",
                "provider": completion.provider,
                "model": completion.model,
                "usage": completion.usage,
                "raw": completion.raw_response,
                "signals": parsed.get("signals") if isinstance(parsed.get("signals"), list) else [],
            },
        )

    def _validate_upload(self, *, filename: str, content: bytes, mime_type: str) -> None:
        if not content:
            raise SkillValidationError("上传素材不能为空。")
        if len(content) > self._max_upload_bytes():
            raise SkillValidationError(
                "上传素材超过大小限制。",
                details={"max_bytes": self._max_upload_bytes(), "size_bytes": len(content)},
            )
        if not filename:
            raise SkillValidationError("上传素材文件名不能为空。")
        if not mime_type:
            raise SkillValidationError("上传素材类型不能为空。")

    def _max_upload_bytes(self) -> int:
        return int(getattr(self.settings, "raw_material_max_upload_bytes", self.settings.test_data_max_upload_bytes))

    def _extract_text_budget_chars(self) -> int:
        return int(getattr(self.settings, "raw_material_extract_text_max_chars", 80_000))

    def _url_timeout_seconds(self) -> float:
        return float(getattr(self.settings, "raw_material_url_timeout_seconds", 20.0))


def parse_generated_skill_draft(content: str) -> GeneratedSkillDraft:
    parsed = _parse_json_object(content)
    raw_files = parsed.get("files")
    if not isinstance(raw_files, dict):
        raise SkillValidationError("Skill 创建智能体响应缺少 files 对象。")
    files: dict[str, str] = {}
    for path, value in raw_files.items():
        normalized_path = normalize_generated_path(str(path))
        if normalized_path == "skill.yaml":
            continue
        files[normalized_path] = str(value)
    missing = [path for path in REQUIRED_GENERATED_FILES if not files.get(path)]
    if missing:
        raise SkillValidationError("Skill 创建智能体响应缺少必需文件。", details={"missing_files": missing})
    review_notes = parsed.get("review_notes")
    material_usage = parsed.get("material_usage")
    return GeneratedSkillDraft(
        files=files,
        generation_reason=str(parsed.get("generation_reason") or "").strip(),
        review_notes=[str(item) for item in review_notes] if isinstance(review_notes, list) else [],
        material_usage=[item for item in material_usage if isinstance(item, dict)] if isinstance(material_usage, list) else [],
        directory_tree=str(parsed.get("directory_tree") or "").strip(),
        raw_parsed=parsed,
    )


def normalize_generated_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").lstrip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise SkillValidationError("生成文件路径非法。", details={"path": value})
    return "/".join(parts)


def infer_material_kind(filename: str, mime_type: str, *, source_url: str | None = None) -> str:
    if source_url:
        return "url"
    lower = mime_type.lower()
    if lower.startswith("text/") or _is_textual(filename, mime_type):
        return "text"
    if lower.startswith("image/"):
        return "image"
    if lower.startswith("audio/"):
        return "audio"
    if lower.startswith("video/"):
        return "video"
    if _is_pdf(filename, mime_type):
        return "pdf"
    return "file"


def _safe_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "upload.bin"


def _filename_from_url(source_url: str, mime_type: str) -> str:
    path = urlparse(source_url).path.rstrip("/")
    candidate = path.rsplit("/", 1)[-1] if path else ""
    if candidate:
        return candidate
    extension = mimetypes.guess_extension(mime_type) or ".html"
    return f"reference-url{extension}"


def _is_textual(filename: str, mime_type: str) -> bool:
    lower_name = filename.lower()
    lower_mime = mime_type.lower()
    return (
        lower_mime.startswith("text/")
        or lower_mime in {"application/json", "application/xml", "application/x-yaml", "application/yaml"}
        or lower_name.endswith((".md", ".markdown", ".txt", ".log", ".json", ".yaml", ".yml", ".csv", ".html", ".htm"))
    )


def _is_html(filename: str, mime_type: str) -> bool:
    return mime_type.lower() in {"text/html", "application/xhtml+xml"} or filename.lower().endswith((".html", ".htm"))


def _is_pdf(filename: str, mime_type: str) -> bool:
    return mime_type.lower() == "application/pdf" or filename.lower().endswith(".pdf")


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SkillsGatewayError("当前环境缺少 pypdf，无法解析 PDF 素材。") from exc
    reader = PdfReader(io.BytesIO(content))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    text = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    if not text:
        raise SkillValidationError("PDF 未提取到可读文本。")
    return text


def _extract_video_frame(content: bytes, filename: str) -> bytes:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SkillsGatewayError("当前环境缺少 imageio-ffmpeg，无法抽取视频帧。") from exc
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / f"input{suffix}"
        output_path = Path(temp_dir) / "frame.jpg"
        input_path.write_bytes(content)
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        result = subprocess.run(command, check=False, capture_output=True)
        if result.returncode != 0 or not output_path.exists():
            raise SkillsGatewayError("视频抽帧失败。", details={"stderr": result.stderr.decode("utf-8", errors="replace")})
        return output_path.read_bytes()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "br", "li", "section", "article", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "li", "section", "article"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(self._chunks))).strip()


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(value)
    return parser.text()


def _text_extraction(value: str, *, source: str) -> MaterialExtraction:
    text = value.strip()
    return MaterialExtraction(
        status="ready",
        parse_summary=_truncate(text.replace("\n", " "), 1200) or "文本素材解析完成。",
        extracted_text=_truncate(text, 80_000),
        processing_metadata={"processor": source, "extracted_chars": len(text)},
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    json_text = _extract_json(content)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise SkillValidationError("智能体响应不是合法 JSON。", details={"error": exc.msg}) from exc
    if not isinstance(parsed, dict):
        raise SkillValidationError("智能体 JSON 响应顶层必须是对象。")
    return parsed


def _extract_json(content: str) -> str:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        return stripped[first : last + 1]
    return stripped


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 20].rstrip() + "\n...[truncated]"


def checksum_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
