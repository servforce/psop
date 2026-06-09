from __future__ import annotations


class AgentHarnessEventTypes:
    RUNNER_STARTED = "agent.runner.started"
    INPUT_GUARDRAIL_CHECKED = "agent.input_guardrail.checked"
    INPUT_GUARDRAIL_FAILED = "agent.input_guardrail.failed"
    SKILLS_HYDRATED = "agent.skills.hydrated"
    SKILLS_ACTIVATED = "agent.skills.activated"
    SANDBOX_POLICY_SELECTED = "agent.sandbox.policy_selected"
    MEMORY_RETRIEVED = "agent.memory.retrieved"
    PLAN_CREATED = "agent.plan.created"
    MODEL_CALL_COMPLETED = "agent.model_call.completed"
    MODEL_CALL_FAILED = "agent.model_call.failed"
    OUTPUT_GUARDRAIL_CHECKED = "agent.output_guardrail.checked"
    OUTPUT_GUARDRAIL_FAILED = "agent.output_guardrail.failed"
    TOOL_GUARDRAIL_CHECKED = "agent.tool_guardrail.checked"
    TOOL_GUARDRAIL_FAILED = "agent.tool_guardrail.failed"
    FINAL_OUTPUT = "agent.final_output"
    FAILED = "agent.failed"
    TOOL_CALL_BLOCKED = "agent.tool_call.blocked"
    TOOL_CALL_FAILED = "agent.tool_call.failed"
    TOOL_CALL_SUCCEEDED = "agent.tool_call.succeeded"
    MEMORY_CANDIDATES_WRITTEN = "agent.memory_candidates.written"
    RUNNER_RESUMED_AUTHORIZED_TOOL = "agent.runner.resumed_authorized_tool"

    TOOL_EXECUTION_STARTED = "tool.execution_started"
    TOOL_EXECUTION_FAILED = "tool.execution_failed"
    TOOL_EXECUTION_SUCCEEDED = "tool.execution_succeeded"
    TOOL_AUTHORIZATION_EXECUTED = "tool.authorization_executed"

    COMPILER_FORMAL_V5_VALIDATED = "compiler.formal_v5.validated"
