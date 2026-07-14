from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import get_inference_gateway
from app.gateway.inference import LlmInferenceGateway


router = APIRouter(prefix="/gateway/inference", tags=["gateway"])


class InferenceModelCapabilityResponse(BaseModel):
    route_key: str
    provider: str
    model: str
    api_base_url: str
    supports_text: bool
    supports_attachments: bool
    thinking_enabled: bool
    thinking_budget: int | None = None


@router.get("/models", response_model=list[InferenceModelCapabilityResponse])
def list_inference_models(
    inference_gateway: LlmInferenceGateway = Depends(get_inference_gateway),
) -> list[InferenceModelCapabilityResponse]:
    return [
        InferenceModelCapabilityResponse(
            route_key=item.route_key,
            provider=item.provider,
            model=item.model,
            api_base_url=item.api_base_url,
            supports_text=item.supports_text,
            supports_attachments=item.supports_attachments,
            thinking_enabled=item.thinking_enabled,
            thinking_budget=item.thinking_budget,
        )
        for item in inference_gateway.list_model_capabilities()
    ]
