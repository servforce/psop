from fastapi import APIRouter

from app.api.routes.compiler import router as compiler_router
from app.api.routes.runtime import gateway_router, replay_router, runtime_router, runs_router
from app.api.routes.skills import router as skills_router
from app.api.routes.system import router as system_router


api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(skills_router)
api_router.include_router(compiler_router)
api_router.include_router(gateway_router)
api_router.include_router(runs_router)
api_router.include_router(replay_router)
api_router.include_router(runtime_router)
