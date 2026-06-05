from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.pskills.exceptions import SkillValidationError, SkillsGatewayError
from app.gateway.inference import LlmAttachment, LlmCompletion, LlmInferenceGateway, MULTIMODAL_ROUTE_KEY
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
class MaterialAnalysisResult:
    status: str
    analysis_result: dict[str, Any] = field(default_factory=dict)
    error_details: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""

@dataclass(frozen=True, slots=True)
class StoredMaterial:
    stored: StoredObject
    artifact_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GeneratedSkillDraft:
    files: dict[str, str]
    generation_reason: str
    review_notes: list[str]
    material_usage: list[dict[str, Any]]
    selected_reference_assets: list[dict[str, Any]]
    directory_tree: str
    raw_parsed: dict[str, Any]


class MaterialProcessor:
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

    def store(
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
    ) -> StoredMaterial:
        self._validate_upload(filename=filename, content=content, mime_type=mime_type)
        safe_filename = _safe_filename(filename)
        object_key = "/".join(["pskill-materials", skill_id, f"{uuid.uuid4()}-{safe_filename}"])
        stored = self.object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=mime_type,
            metadata={
                "skill_id": skill_id,
                "filename": safe_filename,
                "material_kind": material_kind,
                "source": "pskill_material",
            },
        )
        artifact_payload = {
            "kind": "pskill_material",
            "filename": safe_filename,
            "name": name,
            "description": description,
            "material_kind": material_kind,
            "source_note": source_note,
            "metadata": stored.metadata,
        }
        return StoredMaterial(stored=stored, artifact_payload=artifact_payload)

    def analyze(
        self,
        *,
        material_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str,
        description: str,
        material_kind: str,
        source_note: str,
    ) -> MaterialAnalysisResult:
        try:
            if _is_textual(filename, mime_type):
                extracted = _decode_text(content)
                if _is_html(filename, mime_type):
                    extracted = _html_to_text(extracted)
                return _text_analysis(
                    extracted,
                    source="local_text",
                    material_id=material_id,
                    filename=filename,
                    mime_type=mime_type,
                    name=name,
                    description=description,
                    material_kind=material_kind,
                    source_note=source_note,
                    budget_chars=self._extract_text_budget_chars(),
                )
            if _is_pdf(filename, mime_type):
                return _text_analysis(
                    _extract_pdf_text(content),
                    source="local_pdf",
                    material_id=material_id,
                    filename=filename,
                    mime_type=mime_type,
                    name=name,
                    description=description,
                    material_kind=material_kind,
                    source_note=source_note,
                    budget_chars=self._extract_text_budget_chars(),
                )
            if mime_type.startswith("image/"):
                return self._multimodal_extraction(
                    material_id=material_id,
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
                    material_id=material_id,
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
            return MaterialAnalysisResult(
                status="ready",
                analysis_result=_metadata_analysis_result(
                    material_id=material_id,
                    filename=filename,
                    mime_type=mime_type,
                    name=name,
                    description=description,
                    material_kind=material_kind,
                    source_note=source_note,
                    summary="素材已保存。当前类型没有可提取文本或视觉内容。",
                    limitations=["当前文件类型暂无专用解析器。"],
                    debug={"processor": "metadata_only"},
                ),
            )
        except Exception as exc:
            error_details = {
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                **(dict(getattr(exc, "details", {}) or {}) if isinstance(exc, (SkillValidationError, SkillsGatewayError)) else {}),
            }
            return MaterialAnalysisResult(
                status="failed",
                analysis_result=_metadata_analysis_result(
                    material_id=material_id,
                    filename=filename,
                    mime_type=mime_type,
                    name=name,
                    description=description,
                    material_kind=material_kind,
                    source_note=source_note,
                    summary="素材解析失败。",
                    limitations=[str(exc)],
                    debug={"processor": "failed", "error_type": exc.__class__.__name__},
                ),
                error_details=error_details,
                error_message=str(exc),
            )

    def _multimodal_extraction(
        self,
        *,
        material_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        prompt_context: dict[str, Any],
    ) -> MaterialAnalysisResult:
        if not hasattr(self.inference_gateway, "complete_multimodal"):
            raise SkillsGatewayError("当前 LLM Inference Gateway 不支持多模态素材解析。")
        system_prompt = (
            "你是现实操作素材的信息抽取助手。你的任务是从用户上传的素材中提取可用于编写操作指南的信息。"
            "只依据附件中可见、可读或可听的内容；无法确认的内容写“无法判断”，不要臆测品牌、步骤或场景。"
            "关注素材中和实际任务相关的主体内容；忽略平台水印、频道标识、播放控件、装饰性 logo 等无关覆盖层。"
            "但不要忽略字幕、箭头标注、接口名称、警示文字、参数标签等会影响操作理解的信息。"
            "重点提取：画面主体、对象/设备、工具/材料、可见状态、注意事项、安全提示和可引用证据。"
            "不要把素材拆解成最终 Skill 的完整任务步骤，也不要输出任务轨迹字段。"
            "必须只输出合法 JSON，不要使用 Markdown 或解释文字。"
            "格式：{\"summary\":\"一句话概括素材内容\","
            "\"content\":{\"text\":\"附件中可读或可听文本，没有则为空字符串\",\"language\":\"\"},"
            "\"evidence_items\":[{\"kind\":\"visual_observation\",\"content\":\"观察到的事实\","
            "\"observations\":[\"细节\"]}],"
            "\"signals\":[\"工具、设备、接口、警示等线索\"],"
            "\"limitations\":[\"无法确认或质量限制\"]}。"
        )
        user_prompt = json.dumps(
            {
                "task": "extract_operational_material_information",
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
            route_key=MULTIMODAL_ROUTE_KEY,
        )
        parsed = _parse_json_object(completion.content)
        summary = str(parsed.get("summary") or "多模态素材解析完成。").strip()
        content_value = parsed.get("content") if isinstance(parsed.get("content"), dict) else {}
        text = str(content_value.get("text") or "").strip()
        evidence_items = _normalize_evidence_items(parsed.get("evidence_items"), default_kind="visual_observation")
        if not evidence_items and text:
            evidence_items = [{"id": "evidence-1", "kind": "text_excerpt", "content": text, "observations": []}]
        if not evidence_items:
            evidence_items = [
                {
                    "id": "evidence-1",
                    "kind": "visual_observation" if mime_type.startswith("image/") else "audio_observation",
                    "content": summary,
                    "observations": [],
                }
            ]
        return MaterialAnalysisResult(
            status="ready",
            analysis_result=_analysis_result(
                material_id=material_id,
                filename=filename,
                mime_type=mime_type,
                name=str(prompt_context.get("name") or filename),
                description=str(prompt_context.get("description") or ""),
                material_kind=str(prompt_context.get("material_kind") or infer_material_kind(filename, mime_type)),
                source_note=str(prompt_context.get("source_note") or ""),
                summary=_truncate(summary, 2000),
                content={
                    "text": _truncate(text, self._extract_text_budget_chars()),
                    "language": str(content_value.get("language") or ""),
                    "source_type": "multimodal",
                },
                evidence_items=evidence_items,
                assets=[],
                signals=_string_list(parsed.get("signals")),
                limitations=_string_list(parsed.get("limitations")),
                debug={
                    "processor": "llm_multimodal",
                    "provider": completion.provider,
                    "model": completion.model,
                    "usage": completion.usage,
                    "raw": completion.raw_response,
                },
            ),
        )

    def _validate_upload(self, *, filename: str, content: bytes, mime_type: str) -> None:
        if not content:
            raise SkillValidationError("上传素材不能为空。")
        max_upload_bytes = self._max_upload_bytes(mime_type)
        if len(content) > max_upload_bytes:
            raise SkillValidationError(
                "上传素材超过大小限制。",
                details={"max_bytes": max_upload_bytes, "size_bytes": len(content), "mime_type": mime_type},
            )
        if not filename:
            raise SkillValidationError("上传素材文件名不能为空。")
        if not mime_type:
            raise SkillValidationError("上传素材类型不能为空。")

    def _max_upload_bytes(self, mime_type: str) -> int:
        if mime_type.lower().startswith("video/"):
            return int(
                getattr(
                    self.settings,
                    "material_video_max_upload_bytes",
                    getattr(self.settings, "material_max_upload_bytes", self.settings.test_data_max_upload_bytes),
                )
            )
        return int(getattr(self.settings, "material_max_upload_bytes", self.settings.test_data_max_upload_bytes))

    def _extract_text_budget_chars(self) -> int:
        return int(getattr(self.settings, "material_extract_text_max_chars", 80_000))

    def _url_timeout_seconds(self) -> float:
        return float(getattr(self.settings, "material_url_timeout_seconds", 20.0))


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
    selected_reference_assets = parsed.get("selected_reference_assets")
    return GeneratedSkillDraft(
        files=files,
        generation_reason=str(parsed.get("generation_reason") or "").strip(),
        review_notes=[str(item) for item in review_notes] if isinstance(review_notes, list) else [],
        material_usage=[item for item in material_usage if isinstance(item, dict)] if isinstance(material_usage, list) else [],
        selected_reference_assets=_normalize_selected_reference_assets(selected_reference_assets),
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


def infer_material_kind(filename: str, mime_type: str) -> str:
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


def _text_analysis(
    value: str,
    *,
    source: str,
    material_id: str,
    filename: str,
    mime_type: str,
    name: str,
    description: str,
    material_kind: str,
    source_note: str,
    budget_chars: int,
) -> MaterialAnalysisResult:
    text = value.strip()
    summary = _truncate(text.replace("\n", " "), 1200) or "文本素材解析完成。"
    return MaterialAnalysisResult(
        status="ready",
        analysis_result=_analysis_result(
            material_id=material_id,
            filename=filename,
            mime_type=mime_type,
            name=name,
            description=description,
            material_kind=material_kind,
            source_note=source_note,
            summary=summary,
            content={
                "text": _truncate(text, budget_chars),
                "language": "",
                "source_type": source,
            },
            evidence_items=[
                {
                    "id": "text-1",
                    "kind": "text_excerpt",
                    "content": _truncate(text, min(8000, budget_chars)),
                    "observations": [],
                }
            ] if text else [],
            assets=[],
            signals=[],
            limitations=[] if text else ["未提取到可用文本。"],
            debug={"processor": source, "extracted_chars": len(text)},
        ),
    )


def _metadata_analysis_result(
    *,
    material_id: str,
    filename: str,
    mime_type: str,
    name: str,
    description: str,
    material_kind: str,
    source_note: str,
    summary: str,
    limitations: list[str],
    debug: dict[str, Any],
) -> dict[str, Any]:
    return _analysis_result(
        material_id=material_id,
        filename=filename,
        mime_type=mime_type,
        name=name,
        description=description,
        material_kind=material_kind,
        source_note=source_note,
        summary=summary,
        content={"text": "", "language": "", "source_type": "metadata"},
        evidence_items=[
            {
                "id": "metadata-1",
                "kind": "metadata",
                "content": f"文件名：{filename}；MIME：{mime_type}",
                "observations": [],
            }
        ],
        assets=[],
        signals=[],
        limitations=limitations,
        debug=debug,
    )


def _analysis_result(
    *,
    material_id: str,
    filename: str,
    mime_type: str,
    name: str,
    description: str,
    material_kind: str,
    source_note: str,
    summary: str,
    content: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    signals: list[str],
    limitations: list[str],
    debug: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "material_type": infer_material_kind(filename, mime_type),
        "source": {
            "material_id": material_id,
            "name": name,
            "description": description,
            "material_kind": material_kind,
            "filename": filename,
            "mime_type": mime_type,
            "source_note": source_note,
        },
        "summary": summary,
        "content": content,
        "evidence_items": _normalize_evidence_items(evidence_items, default_kind="metadata"),
        "assets": assets,
        "signals": signals,
        "limitations": limitations,
        "debug": debug,
    }


def _normalize_evidence_items(value: Any, *, default_kind: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            content = str(item.get("content") or item.get("caption") or item.get("summary") or "").strip()
            observations = item.get("observations")
            timestamp = item.get("timestamp_ms")
            timestamp_payload = {}
            if isinstance(timestamp, (int, float)):
                timestamp_payload["timestamp_ms"] = int(timestamp)
            elif isinstance(timestamp, str) and timestamp.isdigit():
                timestamp_payload["timestamp_ms"] = int(timestamp)
            normalized.append(
                {
                    "id": str(item.get("id") or f"evidence-{index}"),
                    "kind": str(item.get("kind") or default_kind),
                    "content": content,
                    "observations": observations if isinstance(observations, list) else [],
                    **timestamp_payload,
                    **({"asset_id": str(item["asset_id"])} if item.get("asset_id") else {}),
                    **({"reference_path": str(item["reference_path"])} if item.get("reference_path") else {}),
                    **(
                        {"asset_metadata": item["asset_metadata"]}
                        if isinstance(item.get("asset_metadata"), dict)
                        else {}
                    ),
                }
            )
        elif item is not None:
            normalized.append(
                {
                    "id": f"evidence-{index}",
                    "kind": default_kind,
                    "content": str(item),
                    "observations": [],
                }
            )
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
    fenced = _extract_outer_fenced_json(stripped)
    if fenced is not None:
        return fenced
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        return stripped[first : last + 1]
    return stripped


def _extract_outer_fenced_json(content: str) -> str | None:
    lines = content.splitlines()
    if len(lines) < 2:
        return None
    first = lines[0].strip().lower()
    last = lines[-1].strip()
    if not re.fullmatch(r"```(?:json)?", first):
        return None
    if last != "```":
        return None
    return "\n".join(lines[1:-1]).strip()


def _normalize_selected_reference_assets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        if isinstance(item, str) and item.strip():
            normalized.append({"reference_path": item.strip(), "reason": ""})
    return normalized


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 20].rstrip() + "\n...[truncated]"


def checksum_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
