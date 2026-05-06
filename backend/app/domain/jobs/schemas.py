from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RuntimeJobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    payload: dict[str, Any]
    run_id: str | None = None
    compile_request_id: str | None = None
    attempt_no: int
    max_attempts: int
    last_error: str = ""
    created_at: datetime
    updated_at: datetime

