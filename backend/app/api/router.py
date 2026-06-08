from fastapi import APIRouter

from app.api.routes.agents import (
    agents_router,
    agent_runs_router,
    agent_runs_ws_router,
    run_tool_authorizations_router,
    tool_authorizations_router,
    tool_authorizations_ws_router,
)
from app.api.routes.agent_prompts import router as agent_prompts_router
from app.api.routes.compiler import router as compiler_router
from app.api.routes.evaluations import router as evaluations_router
from app.api.routes.governance import router as governance_router
from app.api.routes.inference import router as inference_router
from app.api.routes.memory import router as memory_router
from app.api.routes.observability import router as observability_router
from app.api.routes.runtime import gateway_router, replay_router, runtime_router, runs_router, terminal_router, ws_router
from app.api.routes.skill_packages import router as skill_packages_router
from app.api.routes.skill_tests import router as skill_tests_router, test_run_activity_ws_router
from app.api.routes.skills import pskill_activity_ws_router, router as skills_router
from app.api.routes.system import router as system_router
from app.api.routes.tools import router as tools_router


api_router = APIRouter()
api_router.include_router(system_router)
api_router.include_router(agents_router)
api_router.include_router(agent_runs_router)
api_router.include_router(tool_authorizations_router)
api_router.include_router(run_tool_authorizations_router)
api_router.include_router(agent_prompts_router)
api_router.include_router(skill_packages_router)
api_router.include_router(skills_router, prefix="/pskills")
api_router.include_router(skill_tests_router)
api_router.include_router(tools_router)
api_router.include_router(compiler_router)
api_router.include_router(evaluations_router)
api_router.include_router(governance_router)
api_router.include_router(memory_router)
api_router.include_router(observability_router)
api_router.include_router(inference_router)
api_router.include_router(gateway_router)
api_router.include_router(runs_router)
api_router.include_router(terminal_router)
api_router.include_router(replay_router)
api_router.include_router(runtime_router)
api_router.include_router(ws_router)
api_router.include_router(agent_runs_ws_router)
api_router.include_router(tool_authorizations_ws_router)
api_router.include_router(pskill_activity_ws_router)
api_router.include_router(test_run_activity_ws_router)
