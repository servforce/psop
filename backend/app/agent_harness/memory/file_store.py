from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileMemoryStore:
    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self._write_payload({})

    def read(self, scope: str) -> dict[str, Any]:
        payload = self._read_payload()
        value = payload.get(scope)
        return dict(value) if isinstance(value, dict) else {}

    def write(self, scope: str, key: str, value: Any) -> None:
        payload = self._read_payload()
        scoped = payload.get(scope)
        if not isinstance(scoped, dict):
            scoped = {}
        scoped[key] = value
        payload[scope] = scoped
        self._write_payload(payload)

    def _read_payload(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.memory_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
