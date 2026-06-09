from __future__ import annotations

from typing import Any

from app.agent_harness.definitions.pskill_builder import PSKILL_BUILDER_SPEC
from app.agent_harness.definitions.pskill_compiler import PSKILL_COMPILER_SPEC
from app.agent_harness.definitions.pskill_evaluator import PSKILL_EVALUATOR_SPEC
from app.agent_harness.definitions.pskill_runner import PSKILL_RUNNER_SPEC
from app.agent_harness.definitions.pskill_tester import PSKILL_TESTER_SPEC
from app.agent_harness.definitions.psop_governance import PSOP_GOVERNANCE_SPEC


DEFAULT_COMPILE_AGENT_REF = "skill_compilation/formal_v5_compile/v1"

BUILTIN_AGENT_SPECS: list[dict[str, Any]] = [
    PSKILL_BUILDER_SPEC,
    PSKILL_COMPILER_SPEC,
    PSKILL_TESTER_SPEC,
    PSKILL_RUNNER_SPEC,
    PSKILL_EVALUATOR_SPEC,
    PSOP_GOVERNANCE_SPEC,
]

DEFAULT_AGENT_SKILLS: dict[str, list[str]] = {
    str(spec["key"]): list(spec["allowed_skill_names"])
    for spec in BUILTIN_AGENT_SPECS
}

AGENT_PROMPT_FALLBACKS: dict[str, tuple[str, str]] = {
    "pskill.builder": ("pskill.build.default", "skill_creation/conversational_draft/v1"),
    "pskill.compiler": ("pskill.compile.formal_v5", DEFAULT_COMPILE_AGENT_REF),
    "pskill.tester": ("pskill.test.pre_publish", "skill_test/pre_publish/v1"),
    "pskill.runner": ("pskill.run.node", "runtime_execution/llm_node_fallback/v1"),
    "pskill.evaluator": ("pskill.evaluate.run", "run_evaluation/default/v1"),
    "psop.governance": ("psop.governance.proposal", "governance/proposal/v1"),
}

PROMPT_USAGE_AGENT_KEYS: dict[str, str] = {
    str(usage_key): str(spec["key"])
    for spec in BUILTIN_AGENT_SPECS
    for usage_key in spec["usage_keys"]
}
PROMPT_USAGE_AGENT_KEYS.update(
    {
        "default.skill_creation_agent": "pskill.builder",
        "default.compile_agent": "pskill.compiler",
        "skill_test.semantic_judge": "pskill.tester",
        "runtime.llm_node_fallback": "pskill.runner",
    }
)
