from __future__ import annotations

from pathlib import Path

from app.pskills import video_analysis


def test_extract_keyframes_falls_back_to_first_frame(monkeypatch, tmp_path) -> None:
    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"fake-video")
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()

    monkeypatch.setattr(video_analysis, "_ffmpeg_exe", lambda: "ffmpeg")

    def fake_run(command, check=False, capture_output=True):
        output_path = Path(command[-1])
        if output_path.name == "000000000.jpg":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"jpg")

        class Result:
            returncode = 0
            stderr = b""

        return Result()

    monkeypatch.setattr(video_analysis.subprocess, "run", fake_run)

    paths = video_analysis._extract_keyframes(
        video_path=video_path,
        frame_dir=frame_dir,
        max_keyframes=3,
    )

    assert [(item.timestamp_ms, item.path.name, item.frame_source) for item in paths] == [
        (0, "000000000.jpg", "fallback_first_frame")
    ]


def test_build_frame_candidates_covers_thirty_minute_video() -> None:
    candidates = video_analysis._build_frame_candidates(
        duration_ms=30 * 60 * 1000,
        scene_timestamps_ms=[],
        max_keyframes=120,
    )

    assert len(candidates) == 60
    assert candidates[0].timestamp_ms == 0
    assert candidates[-1].timestamp_ms >= 29 * 60 * 1000
    assert {item.frame_source for item in candidates} == {"timeline_sample"}


def test_select_scene_timestamps_limits_each_minute_bucket() -> None:
    scene_timestamps = video_analysis._select_scene_timestamps_ms(
        duration_ms=3 * 60 * 1000,
        scene_timestamps_ms=[1000, 2000, 3000, 61_000, 62_000, 63_000],
        limit=10,
    )

    assert scene_timestamps == [1000, 2000, 61_000, 62_000]
    assert 3000 not in scene_timestamps
    assert 63_000 not in scene_timestamps
