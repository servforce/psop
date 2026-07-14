from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalContentDescriptor:
    artifact_object_id: str
    bucket: str
    object_key: str
    mime_type: str
    filename: str
    size_bytes: int
    checksum: str

    @property
    def etag(self) -> str:
        value = (self.checksum or self.artifact_object_id).replace('"', "")
        return f'"{value}"'


def is_single_byte_range_syntax(range_header: str) -> bool:
    unit, separator, spec = range_header.partition("=")
    if separator != "=" or unit.strip().lower() != "bytes":
        return False
    spec = spec.strip()
    if not spec or "," in spec or "-" not in spec:
        return False
    start_text, end_text = [part.strip() for part in spec.split("-", 1)]
    if not start_text:
        return end_text.isdigit() and int(end_text) > 0
    return start_text.isdigit() and (not end_text or end_text.isdigit())


def parse_single_byte_range(range_header: str, size: int) -> tuple[int, int] | None:
    if size <= 0 or not is_single_byte_range_syntax(range_header):
        return None
    _, _, spec = range_header.partition("=")
    start_text, end_text = [part.strip() for part in spec.strip().split("-", 1)]
    if not start_text:
        suffix_length = int(end_text)
        return max(size - suffix_length, 0), size - 1
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


def etag_matches(header_value: str | None, etag: str) -> bool:
    if not header_value:
        return False
    for candidate in header_value.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False
