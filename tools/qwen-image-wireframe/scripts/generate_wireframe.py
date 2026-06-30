from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image


PROMPT_VERSION = "qwen-image-wireframe@0.2.0"
TOOL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_ROOT.parents[1]
DEFAULT_PROMPT = TOOL_ROOT / "prompts" / "wireframe-prompt.md"
DEFAULT_ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env")
DEFAULT_PROVIDER = "bailian-qwen-image-edit"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
DEFAULT_SUBMIT_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_MODEL = "qwen-image-2.0-pro"
DEFAULT_REGION = "cn-beijing"
DEFAULT_MAX_INPUT_EDGE = 0
SIZE_CHOICES = ("1024x1024", "1024x1536", "1536x1024", "2048x2048", "auto")
QWEN_IMAGE_EDIT_LEGACY_MODEL = "qwen-image-edit"


class WireframeError(Exception):
    def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.details = details


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str
    base_url: str
    submit_path: str
    model: str
    timeout: float


@dataclass(frozen=True)
class GenerationResult:
    image_bytes: bytes
    image_url: str
    request_id: str | None
    usage: dict[str, Any] | None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a reference image into a clean technical wireframe using Bailian/Qwen image editing.",
    )
    parser.add_argument("--input", required=True, help="Input reference image path.")
    parser.add_argument("--input-url", default=None, help="Optional public URL, OSS URL, or data URL for the input image.")
    parser.add_argument("--output", required=True, help="Output wireframe image path.")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="Prompt markdown file path.")
    parser.add_argument("--size", choices=SIZE_CHOICES, default="auto")
    parser.add_argument("--base-url", default=None, help="Provider base URL. Overrides --workspace-id and region defaults.")
    parser.add_argument("--submit-path", default=None, help=f"Submit path. Defaults to {DEFAULT_SUBMIT_PATH}.")
    parser.add_argument("--workspace-id", default=None, help="Bailian workspace id for the workspace-specific domain.")
    parser.add_argument(
        "--region",
        choices=("cn-beijing", "ap-southeast-1"),
        default=DEFAULT_REGION,
        help="Bailian region used with --workspace-id.",
    )
    parser.add_argument("--image-model", default=None, help=f"Image-edit model id. Defaults to {DEFAULT_MODEL}.")
    parser.add_argument(
        "--max-input-edge",
        type=int,
        default=DEFAULT_MAX_INPUT_EDGE,
        help="Resize uploaded reference image before base64 encoding. Use 0 to disable resizing.",
    )
    parser.add_argument("--n", type=int, default=1, help="Number of images to request. qwen-image-edit supports only 1.")
    parser.add_argument("--seed", type=int, default=None, help="Optional generation seed in [0, 2147483647].")
    parser.add_argument(
        "--negative-prompt",
        default="color, shading, gray fuzzy strokes, hatching, texture, watermark, logo, brand text",
        help="Negative prompt used to constrain the output.",
    )
    parser.add_argument(
        "--no-prompt-extend",
        action="store_true",
        help="Disable prompt_extend. qwen-image-edit does not support prompt_extend, so it is omitted automatically.",
    )
    parser.add_argument("--watermark", action="store_true", help='Add the "Qwen-Image" watermark. Defaults to false.')
    parser.add_argument("--overwrite", action="store_true", help="Overwrite the output file if it exists.")
    parser.add_argument("--retries", type=int, default=2, help="Retry count after the initial API attempt.")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Initial retry delay in seconds.")
    parser.add_argument("--timeout", type=float, default=600.0, help="HTTP timeout in seconds.")
    parser.add_argument("--debug", action="store_true", help="Include exception type in failure JSON.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        payload = failure_payload(exc, args=args)
        if args.debug:
            payload["exception_type"] = exc.__class__.__name__
        print_json(payload)
        return 1

    print_json(result)
    return 0


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input)
    output_path = Path(args.output)
    prompt_path = Path(args.prompt)

    validate_paths(input_path=input_path, output_path=output_path, prompt_path=prompt_path, overwrite=args.overwrite)
    provider_config = resolve_provider_config(args)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    image_ref = args.input_url or data_image_url(input_path, max_edge=args.max_input_edge)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    generation = call_with_retries(
        args=args,
        provider_config=provider_config,
        prompt=prompt,
        image_ref=image_ref,
    )
    output_path.write_bytes(generation.image_bytes)

    payload: dict[str, Any] = {
        "ok": True,
        "input": str(input_path),
        "output": str(output_path),
        "provider": DEFAULT_PROVIDER,
        "api": "qwen-image-edit-http",
        "base_url": provider_config.base_url,
        "submit_url": make_url(provider_config.base_url, provider_config.submit_path),
        "model": provider_config.model,
        "tool": "http.multimodal-generation",
        "size": normalized_size(args.size),
        "max_input_edge": args.max_input_edge,
        "prompt_version": PROMPT_VERSION,
        "image_url": generation.image_url,
    }
    if generation.request_id:
        payload["request_id"] = generation.request_id
    if generation.usage:
        payload["usage"] = generation.usage
    return payload


def validate_paths(*, input_path: Path, output_path: Path, prompt_path: Path, overwrite: bool) -> None:
    if not input_path.exists():
        raise WireframeError(f"Input image does not exist: {input_path}")
    if not input_path.is_file():
        raise WireframeError(f"Input path is not a file: {input_path}")
    if not prompt_path.exists():
        raise WireframeError(f"Prompt file does not exist: {prompt_path}")
    if output_path.exists() and not overwrite:
        raise WireframeError(
            f"Output file already exists: {output_path}",
            hint="Pass --overwrite to replace it.",
        )


def resolve_provider_config(args: argparse.Namespace) -> ProviderConfig:
    api_key = resolve_env_value("DASHSCOPE_API_KEY") or resolve_env_value("BAILIAN_API_KEY") or resolve_env_value("QWEN_API_KEY")
    if not api_key:
        raise WireframeError(
            "DASHSCOPE_API_KEY, BAILIAN_API_KEY, or QWEN_API_KEY is not set",
            hint="Set one API key in the shell environment or in the PSOP root .env file.",
        )

    workspace_id = args.workspace_id or resolve_env_value("BAILIAN_WORKSPACE_ID") or resolve_env_value("DASHSCOPE_WORKSPACE_ID")
    base_url = args.base_url or base_url_from_workspace(workspace_id=workspace_id, region=args.region)
    return ProviderConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        submit_path=args.submit_path or DEFAULT_SUBMIT_PATH,
        model=args.image_model or DEFAULT_MODEL,
        timeout=args.timeout,
    )


def base_url_from_workspace(*, workspace_id: str | None, region: str) -> str:
    if not workspace_id:
        return DEFAULT_BASE_URL
    if region == "ap-southeast-1":
        return f"https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com"
    return f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com"


def resolve_env_value(key: str) -> str | None:
    value = os.getenv(key)
    if value:
        return value
    for env_file in DEFAULT_ENV_FILES:
        value = read_env_file_value(env_file, key)
        if value:
            return value
    return None


def read_env_file_value(env_file: Path, key: str) -> str | None:
    if not env_file.exists():
        return None

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        raw_key, raw_value = line.split("=", 1)
        if raw_key.strip() != key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


def call_with_retries(*, args: argparse.Namespace, provider_config: ProviderConfig, prompt: str, image_ref: str) -> GenerationResult:
    attempts = max(0, args.retries) + 1
    delay = max(0.0, args.retry_delay)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            print_progress(f"Calling Bailian Qwen image edit... attempt {attempt}/{attempts}")
            return call_qwen_image_edit(
                args=args,
                provider_config=provider_config,
                prompt=prompt,
                image_ref=image_ref,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(delay)
            delay = delay * 2 if delay else 0

    if isinstance(last_error, WireframeError):
        raise last_error
    raise WireframeError(
        f"Wireframe generation failed: {last_error}",
        hint=f"The selected provider is {DEFAULT_PROVIDER} using {provider_config.base_url}.",
    ) from last_error


def call_qwen_image_edit(*, args: argparse.Namespace, provider_config: ProviderConfig, prompt: str, image_ref: str) -> GenerationResult:
    headers = {
        "Authorization": f"Bearer {provider_config.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": provider_config.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": image_ref},
                        {"text": prompt},
                    ],
                }
            ]
        },
        "parameters": request_parameters(args=args, model=provider_config.model),
    }

    response = requests.post(
        make_url(provider_config.base_url, provider_config.submit_path),
        headers=headers,
        json=body,
        timeout=provider_config.timeout,
    )
    data = parse_json_response(response)
    image_url = extract_image_url(data)
    if not image_url:
        raise WireframeError(
            "Bailian Qwen image edit response did not include an image URL",
            details={"response": data},
        )
    return GenerationResult(
        image_bytes=download_image_bytes(image_url, timeout=provider_config.timeout),
        image_url=image_url,
        request_id=str(data.get("request_id") or data.get("requestId") or "") or None,
        usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
    )


def request_parameters(*, args: argparse.Namespace, model: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "n": args.n,
        "negative_prompt": args.negative_prompt,
        "watermark": bool(args.watermark),
    }
    if args.seed is not None:
        parameters["seed"] = args.seed

    if model != QWEN_IMAGE_EDIT_LEGACY_MODEL:
        if args.size != "auto":
            parameters["size"] = normalized_size(args.size)
        if not args.no_prompt_extend:
            parameters["prompt_extend"] = True
    elif args.n != 1:
        raise WireframeError(
            "qwen-image-edit supports only n=1",
            hint="Use qwen-image-2.0-pro, qwen-image-edit-max, or qwen-image-edit-plus for multi-image output.",
        )
    return parameters


def normalized_size(size: str) -> str:
    if size == "auto":
        return size
    return size.replace("x", "*")


def parse_json_response(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise WireframeError(
            f"Provider returned non-JSON HTTP {response.status_code}",
            hint=response.text[:500],
        ) from exc

    if response.status_code >= 400:
        raise WireframeError(
            f"Provider returned HTTP {response.status_code}",
            hint=json.dumps(data, ensure_ascii=False)[:1000],
            details={"response": data},
        )
    if data.get("code"):
        raise WireframeError(
            f"Provider returned error code {data.get('code')}",
            hint=str(data.get("message") or ""),
            details={"response": data},
        )
    return data


def extract_image_url(data: Any) -> str | None:
    if isinstance(data, dict):
        image = data.get("image")
        if is_http_image_url(image):
            return str(image)
        for key in ("content", "choices", "results", "images"):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    image_url = extract_image_url(item)
                    if image_url:
                        return image_url
        for key in ("output", "message", "data", "result"):
            image_url = extract_image_url(data.get(key))
            if image_url:
                return image_url
    if isinstance(data, list):
        for item in data:
            image_url = extract_image_url(item)
            if image_url:
                return image_url
    return None


def is_http_image_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.lower()
    return normalized.startswith(("http://", "https://")) and any(
        marker in normalized for marker in (".png", ".jpg", ".jpeg", ".webp")
    )


def data_image_url(path: Path, *, max_edge: int) -> str:
    image_bytes, media_type = encoded_image_bytes(path, max_edge=max_edge)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{media_type};base64,{encoded}"


def encoded_image_bytes(path: Path, *, max_edge: int) -> tuple[bytes, str]:
    if max_edge <= 0:
        return path.read_bytes(), guess_image_media_type(path)

    with Image.open(path) as image:
        width, height = image.size
        longest = max(width, height)
        if longest <= max_edge:
            return path.read_bytes(), guess_image_media_type(path)

        scale = max_edge / longest
        resized_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = image.convert("RGB").resize(resized_size, Image.Resampling.LANCZOS)
        from io import BytesIO

        buffer = BytesIO()
        resized.save(buffer, format="PNG", optimize=True)
        print_progress(f"Resized upload image {path} from {width}x{height} to {resized_size[0]}x{resized_size[1]}.")
        return buffer.getvalue(), "image/png"


def guess_image_media_type(path: Path) -> str:
    media_type, _encoding = mimetypes.guess_type(str(path))
    if media_type and media_type.startswith("image/"):
        return media_type
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def make_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def download_image_bytes(url: str, *, timeout: float) -> bytes:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "psop-qwen-image-wireframe/0.2"})
    response.raise_for_status()
    return response.content


def failure_payload(exc: Exception, *, args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url or base_url_from_workspace(
        workspace_id=args.workspace_id or resolve_env_value("BAILIAN_WORKSPACE_ID") or resolve_env_value("DASHSCOPE_WORKSPACE_ID"),
        region=args.region,
    )
    payload: dict[str, Any] = {
        "ok": False,
        "input": args.input,
        "output": args.output,
        "provider": DEFAULT_PROVIDER,
        "api": "qwen-image-edit-http",
        "base_url": base_url,
        "submit_url": make_url(base_url, args.submit_path or DEFAULT_SUBMIT_PATH),
        "model": args.image_model or DEFAULT_MODEL,
        "prompt_version": PROMPT_VERSION,
        "error": str(exc),
    }
    hint = getattr(exc, "hint", None)
    if hint:
        payload["hint"] = hint
    details = getattr(exc, "details", None)
    if details:
        payload.update(details)
    return payload


def print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
