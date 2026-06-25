from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


PROMPT_VERSION = "image-wireframe-codex@0.1.0"
TOOL_KIND = "reference-image-wireframe"
TOOL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = TOOL_ROOT / "prompts" / "codex-wireframe-prompt.md"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch Codex to derive a wireframe reference image from a source image.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Source image path.")
    parser.add_argument("--output", required=True, type=Path, help="Output image path.")
    parser.add_argument(
        "--metadata",
        type=Path,
        help="Metadata JSON path. Defaults to <output>.<suffix>.json.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Working directory for codex exec. Defaults to the current directory.",
    )
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_BIN", "codex"),
        help="Codex executable. Defaults to CODEX_BIN or 'codex'.",
    )
    parser.add_argument(
        "--codex-arg",
        action="append",
        default=[],
        help="Extra argument passed after 'codex exec'. Repeat for multiple args.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=DEFAULT_PROMPT,
        help="Prompt template path.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Maximum time to wait for the Codex run.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing output file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt and write metadata preview without launching Codex.",
    )
    parser.add_argument(
        "--passthrough",
        action="store_true",
        help="Let codex exec inherit the current terminal stdout/stderr. Useful for manual debugging.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=10,
        help="Print a waiting message every N seconds while codex exec is running. Use 0 to disable.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        payload = run(args)
    except Exception as exc:
        payload = failure_payload(exc, args)
        write_metadata(metadata_path_from_args(args), payload)
        print_json(payload)
        return int(payload.get("exitCode", 1))

    print_json(payload)
    return int(payload.get("exitCode", 0))


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    metadata_path = metadata_path_from_args(args).resolve()
    prompt_path = args.prompt.resolve()
    workdir = args.workdir.resolve()

    validate_inputs(
        input_path=input_path,
        output_path=output_path,
        prompt_path=prompt_path,
        workdir=workdir,
        overwrite=args.overwrite,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(prompt_path, input_path=input_path, output_path=output_path)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    payload = base_payload(
        input_path=input_path,
        output_path=output_path,
        prompt_sha256=prompt_sha256,
        codex_bin=args.codex_bin,
    )

    if args.dry_run:
        payload["ok"] = True
        payload["dryRun"] = True
        payload["prompt"] = prompt
        write_metadata(metadata_path, payload)
        return payload

    started_at = time.time()
    result = run_codex(
        codex_bin=args.codex_bin,
        codex_args=args.codex_arg,
        prompt=prompt,
        workdir=workdir,
        timeout_seconds=args.timeout_seconds,
        passthrough=args.passthrough,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    elapsed_ms = int((time.time() - started_at) * 1000)

    payload["generator"]["elapsedMs"] = elapsed_ms
    payload["generator"]["exitCode"] = result.returncode
    payload["codex"] = {
        "stdout": tail(result.stdout),
        "stderr": tail(result.stderr),
    }

    if result.returncode != 0:
        payload["ok"] = False
        payload["error"] = "codex exec failed"
        write_metadata(metadata_path, payload)
        return payload_with_exit(payload, result.returncode or 1)

    if not output_path.exists():
        payload["ok"] = False
        payload["error"] = "codex exec completed but did not create the output image"
        write_metadata(metadata_path, payload)
        return payload_with_exit(payload, 2)

    if not is_supported_image(output_path):
        payload["ok"] = False
        payload["error"] = "output file exists but is not a valid PNG, JPG/JPEG, or WEBP image"
        write_metadata(metadata_path, payload)
        return payload_with_exit(payload, 3)

    payload["ok"] = True
    payload["derived"]["sha256"] = sha256_file(output_path)
    payload["derived"]["mimeType"] = mime_type_for(output_path)
    write_metadata(metadata_path, payload)
    return payload


def validate_inputs(
    *,
    input_path: Path,
    output_path: Path,
    prompt_path: Path,
    workdir: Path,
    overwrite: bool,
) -> None:
    if not input_path.exists():
        raise ValueError(f"input image does not exist: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"input path is not a file: {input_path}")
    if not is_supported_image(input_path):
        raise ValueError("input must be a valid PNG, JPG/JPEG, or WEBP image")
    if not prompt_path.exists():
        raise ValueError(f"prompt file does not exist: {prompt_path}")
    if not workdir.exists():
        raise ValueError(f"workdir does not exist: {workdir}")
    if output_path.exists() and not overwrite:
        raise ValueError(f"output already exists, pass --overwrite to replace it: {output_path}")
    if output_path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("output suffix must be .png, .jpg, .jpeg, or .webp")


def run_codex(
    *,
    codex_bin: str,
    codex_args: list[str],
    prompt: str,
    workdir: Path,
    timeout_seconds: int,
    passthrough: bool,
    heartbeat_seconds: int,
) -> subprocess.CompletedProcess[str]:
    command = [codex_bin, "exec", *codex_args, prompt]
    log(f"starting codex exec: {codex_bin} exec ...")
    log(f"workdir: {workdir}")
    if passthrough:
        log("passthrough enabled; codex output will be shown directly in this terminal")
        return subprocess.run(
            command,
            cwd=str(workdir),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )

    process = subprocess.Popen(
        command,
        cwd=str(workdir),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    started_at = time.monotonic()
    last_heartbeat = started_at

    while process.poll() is None:
        elapsed = time.monotonic() - started_at
        if timeout_seconds > 0 and elapsed > timeout_seconds:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr)
        if heartbeat_seconds > 0 and time.monotonic() - last_heartbeat >= heartbeat_seconds:
            log(f"codex exec still running ({int(elapsed)}s elapsed); use --passthrough to see codex output")
            last_heartbeat = time.monotonic()
        time.sleep(0.5)

    stdout, stderr = process.communicate()
    log(f"codex exec finished with exit code {process.returncode}")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def build_prompt(prompt_path: Path, *, input_path: Path, output_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8-sig").format(
        input_path=str(input_path),
        output_path=str(output_path),
    )


def base_payload(
    *,
    input_path: Path,
    output_path: Path,
    prompt_sha256: str,
    codex_bin: str,
) -> dict[str, Any]:
    return {
        "kind": TOOL_KIND,
        "source": {
            "path": str(input_path),
            "sha256": sha256_file(input_path),
        },
        "derived": {
            "path": str(output_path),
        },
        "generator": {
            "engine": "codex-agent",
            "command": codex_bin,
            "promptVersion": PROMPT_VERSION,
            "promptSha256": prompt_sha256,
        },
    }


def metadata_path_from_args(args: argparse.Namespace) -> Path:
    if args.metadata:
        return args.metadata
    return args.output.with_suffix(args.output.suffix + ".json")


def failure_payload(exc: Exception, args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    payload = {
        "ok": False,
        "kind": TOOL_KIND,
        "source": {
            "path": str(input_path),
            "sha256": sha256_file(input_path) if input_path.exists() and input_path.is_file() else None,
        },
        "derived": {
            "path": str(output_path),
        },
        "generator": {
            "engine": "codex-agent",
            "command": args.codex_bin,
            "promptVersion": PROMPT_VERSION,
        },
        "error": str(exc),
        "exitCode": 1,
    }
    if isinstance(exc, FileNotFoundError):
        payload["error"] = "Codex executable was not found. Install Codex CLI or pass --codex-bin."
        payload["exitCode"] = 127
    elif isinstance(exc, PermissionError):
        payload["error"] = "Codex executable could not be started due to permission restrictions."
        payload["exitCode"] = 126
    elif isinstance(exc, subprocess.TimeoutExpired):
        payload["error"] = f"Codex run exceeded {args.timeout_seconds} seconds."
        payload["exitCode"] = 124
    return payload


def payload_with_exit(payload: dict[str, Any], exit_code: int) -> dict[str, Any]:
    payload["exitCode"] = exit_code
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_supported_image(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return False
    if not path.exists() or not path.is_file():
        return False
    header = path.read_bytes()[:16]
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def mime_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def log(message: str) -> None:
    print(f"[image-wireframe] {message}", file=sys.stderr, flush=True)


def tail(value: str, limit: int = 12000) -> str:
    return value[-limit:] if value else ""


def write_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))



