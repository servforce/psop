from __future__ import annotations

from typing import Any, Protocol


class MemoryStore(Protocol):
    def read(self, scope: str) -> dict[str, Any]:
        ...

    def write(self, scope: str, key: str, value: Any) -> None:
        ...
