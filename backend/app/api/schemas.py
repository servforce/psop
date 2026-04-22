from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


class ServiceInfoResponse(BaseModel):
    name: str
    version: str
    environment: str
    api_prefix: str
    source_root: str
    mode: str
    modules: list[str]
