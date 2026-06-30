from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local first-pass checks on a generated technical wireframe image.",
    )
    parser.add_argument("--image", required=True, help="Generated wireframe image path.")
    parser.add_argument(
        "--min-light-pixel-ratio",
        type=float,
        default=0.55,
        help="Minimum ratio of very light pixels expected for a white-background diagram.",
    )
    parser.add_argument(
        "--max-saturated-pixel-ratio",
        type=float,
        default=0.08,
        help="Maximum ratio of highly saturated color pixels expected for black-line diagrams.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload = validate(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def validate(args: argparse.Namespace) -> dict[str, Any]:
    image_path = Path(args.image)
    if not image_path.exists():
        return {"ok": False, "image": str(image_path), "error": "Image does not exist"}

    try:
        from PIL import Image
    except ImportError:
        return {
            "ok": False,
            "image": str(image_path),
            "error": "Pillow is not installed",
            "hint": "Install dependencies from tools/qwen-image-wireframe/requirements.txt.",
        }

    with Image.open(image_path) as image:
        rgb_image = image.convert("RGB")
        width, height = rgb_image.size
        pixels = list(rgb_image.getdata())

    total = max(1, len(pixels))
    light_pixels = 0
    saturated_pixels = 0
    dark_pixels = 0

    for red, green, blue in pixels:
        max_channel = max(red, green, blue)
        min_channel = min(red, green, blue)
        brightness = (red + green + blue) / 3
        if brightness >= 235:
            light_pixels += 1
        if max_channel - min_channel >= 50 and max_channel >= 90:
            saturated_pixels += 1
        if brightness <= 80:
            dark_pixels += 1

    light_ratio = light_pixels / total
    saturated_ratio = saturated_pixels / total
    dark_ratio = dark_pixels / total
    warnings: list[str] = []
    if light_ratio < args.min_light_pixel_ratio:
        warnings.append("background may not be plain white/light enough")
    if saturated_ratio > args.max_saturated_pixel_ratio:
        warnings.append("image may contain too much color for black-line wireframe style")
    if dark_ratio < 0.005:
        warnings.append("linework may be too faint or missing")

    return {
        "ok": not warnings,
        "image": str(image_path),
        "width": width,
        "height": height,
        "light_pixel_ratio": round(light_ratio, 4),
        "dark_pixel_ratio": round(dark_ratio, 4),
        "saturated_pixel_ratio": round(saturated_ratio, 4),
        "warnings": warnings,
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
