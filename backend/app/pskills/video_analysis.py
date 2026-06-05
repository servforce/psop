from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pskills.exceptions import SkillsGatewayError
from app.gateway.asr import AsrGateway, AsrTranscription
from app.gateway.inference import LlmAttachment, LlmInferenceGateway, MULTIMODAL_ROUTE_KEY


MAX_ANALYZED_KEYFRAMES = 120
MAX_SKILL_REFERENCE_ASSETS = 12
TIMELINE_TARGET_KEYFRAMES = 60
DEFAULT_TIMELINE_INTERVAL_MS = 30_000
SCENE_BUCKET_MS = 60_000
SCENE_FRAMES_PER_BUCKET = 2
FRAME_DEDUP_WINDOW_MS = 5_000
DESIGNED_VIDEO_DURATION_MS = 30 * 60 * 1000


@dataclass(frozen=True, slots=True)
class ExtractedVideoFrame:
    timestamp_ms: int
    path: Path
    frame_source: str


@dataclass(frozen=True, slots=True)
class VideoKeyframeAnalysis:
    timestamp_ms: int
    filename: str
    content: bytes
    caption: str
    observations: list[Any] = field(default_factory=list)
    frame_source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoAnalysisResult:
    asr: AsrTranscription
    keyframes: list[VideoKeyframeAnalysis]
    duration_ms: int = 0
    limitations: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


def analyze_video_material(
    *,
    filename: str,
    content: bytes,
    asr_gateway: AsrGateway,
    inference_gateway: LlmInferenceGateway,
    max_keyframes: int = MAX_ANALYZED_KEYFRAMES,
) -> VideoAnalysisResult:
    if not content:
        raise SkillsGatewayError("视频素材为空，无法分析。")
    safe_filename = filename.replace("\\", "/").split("/")[-1].strip() or "video.mp4"
    suffix = Path(safe_filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        video_path = root / f"input{suffix}"
        audio_path = root / "audio.mp3"
        frame_dir = root / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(content)

        _extract_audio(video_path=video_path, audio_path=audio_path)
        asr = asr_gateway.transcribe(
            filename=f"{Path(safe_filename).stem or 'video'}-audio.mp3",
            content=audio_path.read_bytes(),
            media_type="audio/mpeg",
        )

        duration_ms = _probe_video_duration_ms(video_path)
        frame_paths = _extract_keyframes(video_path=video_path, frame_dir=frame_dir, max_keyframes=max_keyframes)
        keyframes: list[VideoKeyframeAnalysis] = []
        for frame in frame_paths:
            caption, observations, recognition_metadata = _recognize_keyframe(
                inference_gateway=inference_gateway,
                filename=frame.path.name,
                content=frame.path.read_bytes(),
                timestamp_ms=frame.timestamp_ms,
            )
            metadata = {
                "frame_source": frame.frame_source,
                "timestamp_seconds": round(frame.timestamp_ms / 1000, 3),
                **recognition_metadata,
            }
            keyframes.append(
                VideoKeyframeAnalysis(
                    timestamp_ms=frame.timestamp_ms,
                    filename=f"{frame.timestamp_ms:09d}.jpg",
                    content=frame.path.read_bytes(),
                    caption=caption,
                    observations=observations,
                    frame_source=frame.frame_source,
                    metadata=metadata,
                )
            )

    limitations = []
    if duration_ms > DESIGNED_VIDEO_DURATION_MS and len(keyframes) >= max_keyframes:
        limitations.append("视频超过 30 分钟，候选帧已按全片时长降采样，局部操作细节可能覆盖不足。")
    usage = _aggregate_keyframe_usage(keyframes)
    return VideoAnalysisResult(
        asr=asr,
        keyframes=keyframes,
        duration_ms=duration_ms,
        limitations=limitations,
        debug={
            "max_keyframes": max_keyframes,
            "timeline_target_keyframes": TIMELINE_TARGET_KEYFRAMES,
            "timeline_interval_ms": _timeline_interval_ms(duration_ms, max_keyframes) if duration_ms else 0,
            **({"usage": usage} if usage else {}),
        },
    )


def _extract_audio(*, video_path: Path, audio_path: Path) -> None:
    ffmpeg = _ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        "-f",
        "mp3",
        str(audio_path),
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        raise SkillsGatewayError(
            "视频音频提取失败。",
            details={"stderr": result.stderr.decode("utf-8", errors="replace")},
        )


def _extract_keyframes(*, video_path: Path, frame_dir: Path, max_keyframes: int) -> list[ExtractedVideoFrame]:
    duration_ms = _probe_video_duration_ms(video_path)
    frames: list[ExtractedVideoFrame] = []
    if duration_ms > 0:
        scene_timestamps = _detect_scene_change_timestamps_ms(video_path)
        candidates = _build_frame_candidates(
            duration_ms=duration_ms,
            scene_timestamps_ms=scene_timestamps,
            max_keyframes=max_keyframes,
        )
        for candidate in candidates:
            output_path = frame_dir / f"{candidate.timestamp_ms:09d}.jpg"
            if _extract_frame_at_timestamp(
                video_path=video_path,
                output_path=output_path,
                timestamp_ms=candidate.timestamp_ms,
            ):
                frames.append(
                    ExtractedVideoFrame(
                        timestamp_ms=candidate.timestamp_ms,
                        path=output_path,
                        frame_source=candidate.frame_source,
                    )
                )
        if frames:
            return frames[:max_keyframes]

    first_frame_path = frame_dir / "000000000.jpg"
    if _extract_first_frame(video_path=video_path, output_path=first_frame_path):
        return [ExtractedVideoFrame(timestamp_ms=0, path=first_frame_path, frame_source="fallback_first_frame")]
    raise SkillsGatewayError("视频关键帧抽取失败，未获得可用画面。")


@dataclass(frozen=True, slots=True)
class _FrameCandidate:
    timestamp_ms: int
    frame_source: str


def _build_frame_candidates(
    *,
    duration_ms: int,
    scene_timestamps_ms: list[int],
    max_keyframes: int,
) -> list[_FrameCandidate]:
    timeline = [
        _FrameCandidate(timestamp_ms=timestamp_ms, frame_source="timeline_sample")
        for timestamp_ms in _timeline_sample_timestamps_ms(duration_ms=duration_ms, max_keyframes=max_keyframes)
    ]
    remaining = max(0, max_keyframes - len(timeline))
    scene = [
        _FrameCandidate(timestamp_ms=timestamp_ms, frame_source="scene_change")
        for timestamp_ms in _select_scene_timestamps_ms(
            scene_timestamps_ms=scene_timestamps_ms,
            duration_ms=duration_ms,
            limit=remaining,
        )
    ]
    return _merge_frame_candidates([*timeline, *scene], max_keyframes=max_keyframes)


def _timeline_sample_timestamps_ms(*, duration_ms: int, max_keyframes: int) -> list[int]:
    if duration_ms <= 0 or max_keyframes <= 0:
        return []
    target = max(1, min(TIMELINE_TARGET_KEYFRAMES, max_keyframes))
    interval_ms = _timeline_interval_ms(duration_ms, max_keyframes)
    values = list(range(0, max(1, duration_ms), interval_ms))[:target]
    final_timestamp = max(0, duration_ms - 1000)
    if values and final_timestamp - values[-1] > interval_ms // 2:
        if len(values) < target:
            values.append(final_timestamp)
        else:
            values[-1] = final_timestamp
    return sorted(set(values))


def _timeline_interval_ms(duration_ms: int, max_keyframes: int) -> int:
    if duration_ms <= 0:
        return 0
    target = max(1, min(TIMELINE_TARGET_KEYFRAMES, max_keyframes))
    return max(DEFAULT_TIMELINE_INTERVAL_MS, int(math.ceil(duration_ms / target)))


def _select_scene_timestamps_ms(*, scene_timestamps_ms: list[int], duration_ms: int, limit: int) -> list[int]:
    if limit <= 0:
        return []
    buckets: dict[int, list[int]] = {}
    for timestamp_ms in sorted(set(scene_timestamps_ms)):
        if timestamp_ms < 0 or (duration_ms > 0 and timestamp_ms >= duration_ms):
            continue
        bucket = timestamp_ms // SCENE_BUCKET_MS
        bucket_items = buckets.setdefault(bucket, [])
        if len(bucket_items) < SCENE_FRAMES_PER_BUCKET:
            bucket_items.append(timestamp_ms)
    selected: list[int] = []
    for bucket in sorted(buckets):
        for timestamp_ms in buckets[bucket]:
            selected.append(timestamp_ms)
            if len(selected) >= limit:
                return selected
    return selected


def _merge_frame_candidates(candidates: list[_FrameCandidate], *, max_keyframes: int) -> list[_FrameCandidate]:
    priority = {"scene_change": 0, "timeline_sample": 1, "fallback_first_frame": 2}
    selected: list[_FrameCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.timestamp_ms, priority.get(item.frame_source, 9))):
        close_index = next(
            (
                index
                for index, existing in enumerate(selected)
                if abs(existing.timestamp_ms - candidate.timestamp_ms) <= FRAME_DEDUP_WINDOW_MS
            ),
            None,
        )
        if close_index is None:
            selected.append(candidate)
            continue
        existing = selected[close_index]
        if priority.get(candidate.frame_source, 9) < priority.get(existing.frame_source, 9):
            selected[close_index] = candidate
    return sorted(selected, key=lambda item: item.timestamp_ms)[:max_keyframes]


def _probe_video_duration_ms(video_path: Path) -> int:
    ffmpeg = _ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path)],
        check=False,
        capture_output=True,
    )
    output = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return 0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return int(round(((hours * 60 + minutes) * 60 + seconds) * 1000))


def _detect_scene_change_timestamps_ms(video_path: Path) -> list[int]:
    ffmpeg = _ffmpeg_exe()
    command = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        "scale=320:-2,select='gt(scene,0.25)',showinfo",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    output = result.stderr.decode("utf-8", errors="replace")
    timestamps: list[int] = []
    for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
        timestamps.append(int(round(float(value) * 1000)))
    return timestamps


def _extract_frame_at_timestamp(*, video_path: Path, output_path: Path, timestamp_ms: int) -> bool:
    ffmpeg = _ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{timestamp_ms / 1000:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-vf",
        "scale=960:-2",
        str(output_path),
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def _extract_first_frame(*, video_path: Path, output_path: Path) -> bool:
    ffmpeg = _ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "scale=960:-2",
        "-frames:v",
        "1",
        str(output_path),
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def _recognize_keyframe(
    *,
    inference_gateway: LlmInferenceGateway,
    filename: str,
    content: bytes,
    timestamp_ms: int,
) -> tuple[str, list[Any], dict[str, Any]]:
    system_prompt = (
        "你是视频教程关键帧观察助手。你的任务是客观描述当前这一帧画面，为后续编写操作指南提供视觉证据。"
        "只描述画面中真实可见的对象、接口、工具、手部动作、文字标注、状态变化和风险点；"
        "关注画面中与实际操作相关的主体内容；忽略平台水印、频道标识、播放控件、装饰性 logo 等无关覆盖层。"
        "但不要忽略字幕、箭头标注、接口名称、警示文字、参数标签等会影响操作理解的信息。"
        "同时判断这一帧对后续编写操作指南的证据价值。"
        "不要根据文件名、常识或上下文补全看不见的内容。"
        "如果没有收到图片或画面无法识别，caption 写“无法从当前关键帧识别有效画面”，observations 输出空数组。"
        "必须只输出合法 JSON，不要使用 Markdown 或解释文字。"
        "格式：{\"caption\":\"一句话描述当前画面的关键动作或状态\","
        "\"observations\":[\"具体可见证据\"],"
        "\"frame_type\":\"operation|context|title|logo|transition|unknown\","
        "\"operation_relevance\":\"high|medium|low|none\","
        "\"noise_flags\":[\"watermark|platform_logo|decorative_subtitle|transition|duplicate|low_information\"]}。"
    )
    user_prompt = json.dumps(
        {
            "task": "describe_video_keyframe_for_operational_guide",
            "filename": filename,
            "timestamp_ms": timestamp_ms,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    completion = inference_gateway.complete_multimodal(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        attachments=[
            LlmAttachment(
                filename=filename,
                media_type="image/jpeg",
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        ],
        route_key=MULTIMODAL_ROUTE_KEY,
    )
    parsed = _parse_json_object(completion.content)
    caption = str(parsed.get("caption") or parsed.get("summary") or "").strip()
    observations = parsed.get("observations")
    if not isinstance(observations, list):
        observations = parsed.get("signals") if isinstance(parsed.get("signals"), list) else []
    noise_flags = parsed.get("noise_flags")
    metadata = {
        "frame_type": str(parsed.get("frame_type") or "unknown"),
        "operation_relevance": str(parsed.get("operation_relevance") or "unknown"),
        "noise_flags": [str(item) for item in noise_flags] if isinstance(noise_flags, list) else [],
        "usage": completion.usage,
    }
    return caption or "关键帧包含可用于重建任务步骤的视觉线索。", observations, metadata


def _aggregate_keyframe_usage(keyframes: list[VideoKeyframeAnalysis]) -> dict[str, int] | None:
    totals = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for keyframe in keyframes:
        usage = keyframe.metadata.get("usage")
        if not isinstance(usage, dict):
            continue
        token_seen = False
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += value
                token_seen = True
        if token_seen:
            totals["llm_calls"] += 1
    return totals if totals["llm_calls"] > 0 else None


def _parse_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        stripped = stripped[first : last + 1]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {"caption": content.strip(), "observations": []}
    return parsed if isinstance(parsed, dict) else {"caption": str(parsed), "observations": []}


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SkillsGatewayError("当前环境缺少 imageio-ffmpeg，无法分析视频素材。") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()
