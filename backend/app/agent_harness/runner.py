from __future__ import annotations

import asyncio
import difflib
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent_harness.agent_decision import AgentDecision
from app.agent_harness.guardrails import OutputGuardrail
from app.agent_harness.planning import AgentPlanner
from app.agent_harness.tools import ToolPolicy
from app.agents.models import AgentRun, AgentToolAuthorization
from app.agents.schemas import (
    AppendAgentEventRequest,
    CreateAgentToolCallRequest,
    CreateToolAuthorizationRequest,
    AgentRunResponse,
)
from app.agents.service import AgentService
from app.compiler.formal_v5 import validate_and_normalize_artifact
from app.compiler.service import CompilerService
from app.evaluations.service import EvaluationService
from app.governance.schemas import GovernanceProposalCreateRequest
from app.governance.service import GovernanceService
from app.memory.schemas import MemorySearchRequest
from app.memory.service import MemoryService
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.manifest import SkillDocument, document_from_manifest_snapshot, parse_skill_yaml, render_skill_yaml
from app.pskills.models import now_utc
from app.pskills.service import SkillsService
from app.runtime.service import RuntimeService
from app.runtime.websocket import (
    TOOL_AUTHORIZATION_WS_CHANNEL,
    tool_authorization_ws_hub,
    tool_authorization_ws_message,
)
from app.skills.models import SkillActivation
from app.skills.repository import SkillPackageRepository
from app.skills.service import SkillPackageService
from app.testing.service import SkillTestService


DEFAULT_AGENT_SKILLS: dict[str, list[str]] = {
    "pskill.builder": ["pskill-builder", "ffmpeg-video-processing", "document-ocr-processing"],
    "pskill.compiler": ["pskill-compiler-formal-v5"],
    "pskill.tester": ["pskill-tester"],
    "pskill.runner": ["pskill-runner-field-assistant"],
    "pskill.evaluator": ["pskill-run-evaluator"],
    "psop.governance": ["psop-governance-manager"],
}


class AgentRunner:
    def __init__(
        self,
        *,
        agent_service: AgentService | None = None,
        skill_service: SkillPackageService | None = None,
        skill_repository: SkillPackageRepository | None = None,
        tool_policy: ToolPolicy | None = None,
        memory_service: MemoryService | None = None,
        pskills_service: SkillsService | None = None,
        compiler_service: CompilerService | None = None,
        runtime_service: RuntimeService | None = None,
        evaluation_service: EvaluationService | None = None,
        testing_service: SkillTestService | None = None,
        governance_service: GovernanceService | None = None,
        output_guardrail: OutputGuardrail | None = None,
        planner: AgentPlanner | None = None,
    ) -> None:
        self.agent_service = agent_service or AgentService()
        self.skill_service = skill_service or SkillPackageService()
        self.skill_repository = skill_repository or SkillPackageRepository()
        self.tool_policy = tool_policy or ToolPolicy()
        self.memory_service = memory_service or MemoryService()
        self.pskills_service = pskills_service
        self.compiler_service = compiler_service
        self.runtime_service = runtime_service
        self.evaluation_service = evaluation_service or EvaluationService()
        self.testing_service = testing_service
        self.governance_service = governance_service or GovernanceService()
        self.output_guardrail = output_guardrail or OutputGuardrail()
        self.planner = planner or AgentPlanner()

    def run_once(self, session: Session, agent_run_id: str) -> AgentRunResponse:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        if agent_run.status not in {"queued", "running"}:
            raise SkillValidationError(
                "AgentRun 当前状态不可执行。",
                details={"agent_run_id": agent_run.id, "status": agent_run.status},
            )

        version = self.agent_service.repository.get_version(session, agent_run.agent_version_id)
        if not version:
            raise SkillNotFoundError("未找到 AgentVersion。", details={"agent_run_id": agent_run.id})
        resumed = self._execute_authorized_tool_call(session, agent_run)
        if resumed:
            return resumed
        spec = version.spec_json
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(event_type="agent.runner.started", phase="runner", payload={"agent_key": agent_run.agent_key}),
            commit=False,
        )

        active_tools, active_skill_names = self._activate_skills(session, agent_run=agent_run, spec=spec)
        memory_context = self.memory_service.retrieve_context_for_agent(
            session,
            agent_key=agent_run.agent_key,
            limit=self._memory_context_limit(spec),
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.memory.retrieved",
                phase="memory",
                payload={
                    "memory_entry_ids": [str(item.get("id")) for item in memory_context],
                    "memory_entry_count": len(memory_context),
                    "status": "active",
                    "used_as_runtime_state": False,
                },
            ),
            commit=False,
        )
        plan = self.planner.create_plan(
            agent_key=agent_run.agent_key,
            spec=spec,
            input_payload=agent_run.input_payload,
            active_skill_names=active_skill_names,
            memory_context=memory_context,
        )
        plan_payload = plan.as_dict()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.plan.created",
                phase="planning",
                payload=plan_payload,
            ),
            commit=False,
        )
        decision = self._decision_from_input(agent_run.input_payload)
        self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run.id,
            provider="deterministic",
            route_key=str(spec.get("model_policy", {}).get("route_key") or "json"),
            model_name="agent-harness-deterministic",
            status="succeeded",
            request_payload={
                "input_payload": agent_run.input_payload,
                "agent_key": agent_run.agent_key,
                "memory_context": memory_context,
                "plan": plan_payload,
            },
            response_payload=decision.model_dump(mode="json"),
            usage_json={"input_tokens": 0, "output_tokens": 0},
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.model_call.completed",
                phase="model",
                payload={"decision_type": decision.decision_type},
            ),
            commit=False,
        )

        if decision.decision_type == "final_output":
            guardrail_result = self.output_guardrail.check(
                agent_key=agent_run.agent_key,
                output_payload=decision.output_payload,
            )
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.output_guardrail.checked",
                    phase="guardrails",
                    payload=guardrail_result.as_event_payload(),
                ),
                commit=False,
            )
            if not guardrail_result.passed:
                agent_run.output_payload = {
                    "rejected_output": decision.output_payload,
                    "guardrail_findings": [item.as_dict() for item in guardrail_result.findings],
                }
                agent_run.status = "failed"
                agent_run.error_message = "output_guardrail_failed"
                agent_run.ended_at = now_utc()
                self.agent_service.append_event(
                    session,
                    agent_run.id,
                    AppendAgentEventRequest(
                        event_type="agent.output_guardrail.failed",
                        phase="guardrails",
                        payload={"findings": [item.as_dict() for item in guardrail_result.findings]},
                    ),
                    commit=False,
                )
                session.commit()
                return self.agent_service._build_run_response(agent_run)
            agent_run.output_payload = decision.output_payload
            memory_count = self._write_memory_candidates(session, agent_run=agent_run, output_payload=decision.output_payload)
            agent_run.status = "succeeded"
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.final_output",
                    phase="output",
                    payload={
                        "output_schema": spec.get("output_schema", {}),
                        "memory_candidate_count": memory_count,
                        "business_wait_state": guardrail_result.business_wait_state,
                        "non_hitl_business_state": bool(guardrail_result.business_wait_state),
                    },
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        if decision.decision_type == "fail":
            agent_run.status = "failed"
            agent_run.error_message = decision.error_message or "agent_decision_failed"
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(event_type="agent.failed", phase="output", payload={"error": agent_run.error_message}),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        return self._handle_tool_call(session, agent_run=agent_run, spec=spec, decision=decision, active_tools=active_tools)

    def _execute_authorized_tool_call(self, session: Session, agent_run: AgentRun) -> AgentRunResponse | None:
        tool_calls = self.agent_service.repository.list_tool_calls(session, agent_run.id)
        tool_call = next((item for item in tool_calls if item.status == "authorized"), None)
        if not tool_call:
            return None
        authorization = next(
            (
                item
                for item in self.agent_service.repository.list_tool_authorizations(
                    session,
                    agent_run_id=agent_run.id,
                    status="approved",
                )
                if item.agent_tool_call_id == tool_call.id
            ),
            None,
        )
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        tool_call.status = "executing"
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.runner.resumed_authorized_tool",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
            ),
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="tool.execution_started",
                phase="tool",
                payload={
                    "tool_call_id": tool_call.id,
                    "authorization_id": authorization.id if authorization else "",
                    "tool_name": tool_call.tool_name,
                    "tool_provider": tool_call.tool_provider,
                    "side_effect_level": tool_call.side_effect_level,
                },
            ),
            commit=False,
        )
        try:
            tool_result = self._execute_native_tool_call(session, tool_call=tool_call)
        except (SkillNotFoundError, SkillValidationError) as error:
            tool_call.status = "failed"
            tool_call.result_summary = {"executed": False, "error": error.message, "details": error.details}
            if authorization:
                authorization.status = "executed"
                authorization.executed_at = now_utc()
            agent_run.status = "failed"
            agent_run.error_message = error.message
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="tool.execution_failed",
                    phase="tool",
                    payload={
                        "tool_call_id": tool_call.id,
                        "authorization_id": authorization.id if authorization else "",
                        "tool_name": tool_call.tool_name,
                        "error": error.message,
                    },
                ),
                commit=False,
            )
            if authorization:
                self._append_tool_authorization_executed_event(
                    session,
                    agent_run=agent_run,
                    tool_call=tool_call,
                    authorization=authorization,
                    execution_status="failed",
                    details={"error": error.message},
                )
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.tool_call.failed",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name, "error": error.message},
                ),
                commit=False,
            )
            session.commit()
            if authorization:
                self._broadcast_tool_authorization_executed(authorization)
            return self.agent_service._build_run_response(agent_run)
        tool_call.status = "succeeded"
        tool_call.result_summary = {
            "executed": True,
            "authorization_id": authorization.id if authorization else "",
            **tool_result,
        }
        if authorization:
            authorization.status = "executed"
            authorization.executed_at = now_utc()
        agent_run.status = "succeeded"
        agent_run.output_payload = {
            "tool_result": {"tool_name": tool_call.tool_name, "status": "succeeded", **tool_result}
        }
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="tool.execution_succeeded",
                phase="tool",
                payload={
                    "tool_call_id": tool_call.id,
                    "authorization_id": authorization.id if authorization else "",
                    "tool_name": tool_call.tool_name,
                    "result": tool_result,
                },
            ),
            commit=False,
        )
        if authorization:
            self._append_tool_authorization_executed_event(
                session,
                agent_run=agent_run,
                tool_call=tool_call,
                authorization=authorization,
                execution_status="succeeded",
                details={"result": tool_result},
            )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.tool_call.succeeded",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name, "result": tool_result},
            ),
            commit=False,
        )
        session.commit()
        if authorization:
            self._broadcast_tool_authorization_executed(authorization)
        return self.agent_service._build_run_response(agent_run)

    def _broadcast_tool_authorization_executed(self, authorization: AgentToolAuthorization) -> None:
        response = self.agent_service._build_tool_authorization_response(authorization)
        message = tool_authorization_ws_message(response, action="executed")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(tool_authorization_ws_hub.broadcast(TOOL_AUTHORIZATION_WS_CHANNEL, message))
        else:
            loop.create_task(tool_authorization_ws_hub.broadcast(TOOL_AUTHORIZATION_WS_CHANNEL, message))

    def _append_tool_authorization_executed_event(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        tool_call: Any,
        authorization: AgentToolAuthorization,
        execution_status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="tool.authorization_executed",
                phase="tool_authorization",
                payload={
                    "authorization_id": authorization.id,
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.tool_name,
                    "status": authorization.status,
                    "execution_status": execution_status,
                    "executed_at": authorization.executed_at.isoformat() if authorization.executed_at else None,
                    **(details or {}),
                },
            ),
            commit=False,
        )

    def _execute_native_tool_call(self, session: Session, *, tool_call: Any) -> dict[str, Any]:
        arguments = tool_call.arguments_summary or {}
        if tool_call.tool_name == "psop.pskills.get":
            pskill_id = self._pskill_id_from_arguments(session, arguments)
            detail = self._require_pskills_service().get_skill_detail(session, pskill_id)
            return {"result": detail.model_dump(mode="json")}
        if tool_call.tool_name == "psop.pskills.read":
            pskill_id = self._pskill_id_from_arguments(session, arguments)
            detail = self._require_pskills_service().get_skill_detail(session, pskill_id)
            return {"result": detail.model_dump(mode="json")}
        if tool_call.tool_name == "psop.materials.list":
            pskill_id = self._pskill_id_from_arguments(session, arguments)
            materials = self._require_pskills_service().list_materials(session, skill_id=pskill_id)
            return {
                "result": {
                    "material_count": len(materials),
                    "materials": [item.model_dump(mode="json") for item in materials],
                }
            }
        if tool_call.tool_name == "psop.materials.read_analysis":
            pskill_id = self._pskill_id_from_arguments(session, arguments)
            material_id = self._required_tool_argument(arguments, "material_id")
            analysis = self._require_pskills_service().get_material_analysis(
                session,
                skill_id=pskill_id,
                material_id=material_id,
            )
            return {"result": analysis.model_dump(mode="json")}
        if tool_call.tool_name == "psop.repository.read_file":
            pskill_id = self._pskill_id_from_arguments(session, arguments)
            path = self._required_tool_argument(arguments, "path", "file_path")
            file_response = self._require_pskills_service().get_repository_file(session, skill_id=pskill_id, path=path)
            return {"result": file_response.model_dump(mode="json")}
        if tool_call.tool_name == "psop.repository.propose_patch":
            return {"result": self._propose_repository_patch(session, arguments)}
        if tool_call.tool_name == "psop.pskill_manifest.parse":
            content = self._required_tool_argument(arguments, "content", "skill_yaml_content")
            document = parse_skill_yaml(content)
            return {"result": {"document": document.model_dump(mode="json"), "manifest": document.skill.model_dump(mode="json")}}
        if tool_call.tool_name == "psop.pskill_manifest.render":
            manifest = arguments.get("document", arguments.get("manifest", arguments.get("snapshot")))
            if not isinstance(manifest, dict):
                raise SkillValidationError("psop.pskill_manifest.render 缺少 manifest。", details={"arguments_summary": arguments})
            document = self._document_from_tool_manifest(manifest)
            return {"result": {"content": render_skill_yaml(document), "document": document.model_dump(mode="json")}}
        if tool_call.tool_name == "psop.compiler.validate_formal_v5":
            return {"result": self._validate_formal_v5_artifact(session, tool_call=tool_call, arguments=arguments)}
        if tool_call.tool_name == "psop.testing.write_diagnostics":
            return {"result": self._write_testing_diagnostics(session, tool_call=tool_call, arguments=arguments)}
        if tool_call.tool_name == "psop.memory.search":
            try:
                payload = MemorySearchRequest(
                    query=str(arguments.get("query") or arguments.get("q") or ""),
                    namespace=arguments.get("namespace"),
                    memory_type=arguments.get("memory_type"),
                    status=arguments.get("status", "active"),
                    agent_key=arguments.get("agent_key"),
                    limit=int(arguments.get("limit") or 25),
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise SkillValidationError(
                    "psop.memory.search 参数无效。",
                    details={"arguments_summary": arguments, "error": str(error)},
                ) from error
            entries = self.memory_service.search(session, payload)
            return {
                "result": {
                    "memory_entry_count": len(entries),
                    "memory_entry_ids": [item.id for item in entries],
                    "entries": [item.model_dump(mode="json") for item in entries],
                }
            }
        if tool_call.tool_name == "psop.memory.write_candidate":
            agent_run = self.agent_service.get_run_model(session, tool_call.agent_run_id)
            candidates = arguments.get("candidates", arguments.get("memory_candidates", []))
            if isinstance(candidates, dict):
                candidates = [candidates]
            if not isinstance(candidates, list) or not candidates:
                raise SkillValidationError(
                    "psop.memory.write_candidate 缺少 candidates。",
                    details={"arguments_summary": arguments},
                )
            guardrail_result = self.output_guardrail.check(
                agent_key=agent_run.agent_key,
                output_payload={"memory_candidates": candidates},
            )
            if not guardrail_result.passed:
                raise SkillValidationError(
                    "memory_candidate_guardrail_failed",
                    details={"findings": [item.as_dict() for item in guardrail_result.findings]},
                )
            try:
                entries = self.memory_service.write_candidates(
                    session,
                    agent_key=agent_run.agent_key,
                    created_by_agent_run_id=agent_run.id,
                    candidates=candidates,
                    commit=False,
                )
            except ValidationError as error:
                raise SkillValidationError(
                    "psop.memory.write_candidate 参数无效。",
                    details={"arguments_summary": arguments, "error": str(error)},
                ) from error
            return {
                "result": {
                    "memory_entry_count": len(entries),
                    "memory_entry_ids": [item.id for item in entries],
                    "entries": [item.model_dump(mode="json") for item in entries],
                }
            }
        if tool_call.tool_name == "psop.runtime.read":
            return {"result": self._read_runtime_facts(session, tool_call=tool_call, arguments=arguments)}
        if tool_call.tool_name == "psop.evaluations.read":
            return {"result": self._read_evaluation_facts(session, arguments)}
        if tool_call.tool_name == "psop.evaluations.write_diagnostics":
            return {"result": self._write_evaluation_diagnostics(session, tool_call=tool_call, arguments=arguments)}
        if tool_call.tool_name == "psop.governance.write_proposal":
            proposal_arguments = arguments.get("proposal", arguments)
            if not isinstance(proposal_arguments, dict):
                raise SkillValidationError(
                    "psop.governance.write_proposal 参数必须是对象。",
                    details={"arguments_summary": arguments},
                )
            try:
                payload = GovernanceProposalCreateRequest(**proposal_arguments)
            except ValidationError as error:
                raise SkillValidationError(
                    "psop.governance.write_proposal 参数无效。",
                    details={"arguments_summary": arguments, "error": str(error)},
                ) from error
            proposal = self.governance_service.create_proposal_from_agent_tool(
                session,
                agent_run_id=tool_call.agent_run_id,
                payload=payload,
                commit=False,
            )
            return {
                "result": {
                    "proposal_id": proposal.id,
                    "proposal": proposal.model_dump(mode="json"),
                }
            }
        if tool_call.tool_name == "psop.agent_version.activate":
            agent_key = str(arguments.get("agent_key") or "").strip()
            version_id = str(arguments.get("version_id") or "").strip()
            if not agent_key or not version_id:
                raise SkillValidationError(
                    "psop.agent_version.activate 缺少 agent_key 或 version_id。",
                    details={"arguments_summary": arguments},
                )
            activation = self.agent_service.activate_version_from_tool(
                session,
                agent_key=agent_key,
                version_id=version_id,
                commit=False,
            )
            return {"result": activation}
        if tool_call.tool_name == "psop.skill_version.activate":
            package_name = str(
                arguments.get("package_name") or arguments.get("skill_package") or arguments.get("skill_name") or ""
            ).strip()
            version_id = str(arguments.get("version_id") or "").strip()
            if not package_name or not version_id:
                raise SkillValidationError(
                    "psop.skill_version.activate 缺少 package_name 或 version_id。",
                    details={"arguments_summary": arguments},
                )
            activation = self.skill_service.activate_version_from_tool(
                session,
                package_name=package_name,
                version_id=version_id,
                commit=False,
            )
            return {"result": activation}
        raise SkillValidationError(
            "native_tool_not_implemented",
            details={"tool_name": tool_call.tool_name},
        )

    def _propose_repository_patch(self, session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
        pskill_id = self._pskill_id_from_arguments(session, arguments)
        files = arguments.get("files", arguments.get("proposed_files"))
        if not isinstance(files, dict) or not files:
            raise SkillValidationError(
                "psop.repository.propose_patch 缺少 files。",
                details={"arguments_summary": arguments},
            )

        file_changes: list[dict[str, Any]] = []
        combined_diff: list[str] = []
        base_commit_sha = str(arguments.get("base_commit_sha") or "")
        current_files = arguments.get("current_files", {})
        if not isinstance(current_files, dict):
            current_files = {}
        for raw_path, proposed_content in files.items():
            path = str(raw_path).strip()
            if not path:
                raise SkillValidationError("psop.repository.propose_patch 包含空文件路径。")
            if not isinstance(proposed_content, str):
                raise SkillValidationError(
                    "psop.repository.propose_patch 文件内容必须是字符串。",
                    details={"path": path},
                )
            current_content = ""
            current_head = ""
            change_type = "create"
            if path in current_files:
                current_content = str(current_files[path] or "")
                current_head = base_commit_sha
                change_type = "modify" if current_content else "create"
            else:
                try:
                    current_file = self._require_pskills_service().get_repository_file(session, skill_id=pskill_id, path=path)
                    current_content = current_file.content
                    current_head = current_file.head_commit_sha
                    change_type = "modify"
                except SkillNotFoundError:
                    current_head = base_commit_sha
            diff_lines = list(
                difflib.unified_diff(
                    current_content.splitlines(keepends=True),
                    proposed_content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            diff_text = "\n".join(line.rstrip("\n") for line in diff_lines)
            combined_diff.extend(diff_lines)
            file_changes.append(
                {
                    "path": path,
                    "change_type": change_type,
                    "base_commit_sha": base_commit_sha or current_head,
                    "head_commit_sha": current_head,
                    "proposed_content": proposed_content,
                    "diff": diff_text,
                    "changed": current_content != proposed_content,
                }
            )
        return {
            "status": "patch_proposed",
            "pskill_id": pskill_id,
            "summary": str(arguments.get("summary") or arguments.get("draft_summary") or ""),
            "file_change_count": len(file_changes),
            "file_changes": file_changes,
            "diff": "\n".join(line.rstrip("\n") for line in combined_diff),
            "committed": False,
            "requires_human_apply": True,
            "commit_tool": "psop.repository.commit_patch",
        }

    def _require_pskills_service(self) -> SkillsService:
        if not self.pskills_service:
            raise SkillValidationError("AgentRunner 未配置 PSkill service，无法执行 PSkill repository 工具。")
        return self.pskills_service

    def _require_compiler_service(self) -> CompilerService:
        if not self.compiler_service:
            raise SkillValidationError("AgentRunner 未配置 Compiler service，无法执行 Compiler 工具。")
        return self.compiler_service

    def _require_runtime_service(self) -> RuntimeService:
        if not self.runtime_service:
            raise SkillValidationError("AgentRunner 未配置 Runtime service，无法执行 Runtime 工具。")
        return self.runtime_service

    def _require_testing_service(self) -> SkillTestService:
        if not self.testing_service:
            raise SkillValidationError("AgentRunner 未配置 Testing service，无法执行 Testing 工具。")
        return self.testing_service

    def _validate_formal_v5_artifact(self, session: Session, *, tool_call: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(arguments.get("artifact_id") or arguments.get("compile_artifact_id") or "").strip()
        if artifact_id:
            validation = self._require_compiler_service().validate_artifact(session, artifact_id)
            result = validation.model_dump(mode="json")
        else:
            candidate = arguments.get("artifact", arguments.get("candidate", arguments.get("compile_artifact")))
            if not isinstance(candidate, dict):
                raise SkillValidationError(
                    "psop.compiler.validate_formal_v5 缺少 artifact 或 artifact_id。",
                    details={"required_any_of": ["artifact_id", "compile_artifact_id", "artifact"], "arguments_summary": arguments},
                )
            validation = validate_and_normalize_artifact(candidate)
            result = {
                "artifact_id": "",
                "compile_request_id": "",
                "pskill_version_id": "",
                "valid": not validation.has_errors and validation.artifact is not None,
                "diagnostics": [item.as_dict() for item in validation.diagnostics],
                "graph_summary": validation.artifact.get("graph_summary") if validation.artifact else None,
                "capability_summary": validation.artifact.get("capability_summary") if validation.artifact else None,
                "normalized_artifact": validation.artifact,
            }
        self.agent_service.append_event(
            session,
            tool_call.agent_run_id,
            AppendAgentEventRequest(
                event_type="compiler.formal_v5.validated",
                phase="compiler",
                payload={
                    "artifact_id": result.get("artifact_id") or artifact_id,
                    "valid": result.get("valid"),
                    "diagnostic_count": len(result.get("diagnostics") or []),
                },
            ),
            commit=False,
        )
        return result

    def _write_testing_diagnostics(self, session: Session, *, tool_call: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._require_testing_service().write_diagnostics_from_agent_tool(
            session,
            agent_run_id=tool_call.agent_run_id,
            payload=arguments,
            commit=False,
        )

    def _write_evaluation_diagnostics(self, session: Session, *, tool_call: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        evaluation_id = str(arguments.get("evaluation_id") or "").strip()
        if not evaluation_id:
            agent_run = self.agent_service.get_run_model(session, tool_call.agent_run_id)
            if agent_run.owner_type == "run_evaluation":
                evaluation_id = str(agent_run.owner_id or "").strip()
        if not evaluation_id:
            raise SkillValidationError(
                "psop.evaluations.write_diagnostics 缺少 evaluation_id。",
                details={"required_any_of": ["arguments_summary.evaluation_id", "agent_run.owner_id"]},
            )
        return self.evaluation_service.write_diagnostics_from_agent_tool(
            session,
            agent_run_id=tool_call.agent_run_id,
            evaluation_id=evaluation_id,
            payload=arguments,
            commit=False,
        )

    def _read_evaluation_facts(self, session: Session, arguments: dict[str, Any]) -> dict[str, Any]:
        evaluation_id = str(arguments.get("evaluation_id") or "").strip()
        finding_id = str(arguments.get("finding_id") or "").strip()
        if evaluation_id:
            evaluation = self.evaluation_service.get_evaluation(session, evaluation_id)
            return {
                "mode": "evaluation",
                "evaluation": evaluation.model_dump(mode="json"),
                "finding_count": len(evaluation.findings),
            }
        if finding_id:
            finding = self.evaluation_service.get_finding(session, finding_id)
            return {
                "mode": "finding",
                "finding": finding.model_dump(mode="json"),
            }
        findings = self.evaluation_service.list_findings(
            session,
            status=self._optional_tool_string(arguments.get("status")),
            category=self._optional_tool_string(arguments.get("category")),
            severity=self._optional_tool_string(arguments.get("severity")),
            run_id=self._optional_tool_string(arguments.get("run_id")),
            pskill_definition_id=self._optional_tool_string(arguments.get("pskill_definition_id")),
        )
        limit = self._bounded_tool_limit(arguments.get("limit"), default=25, maximum=100)
        selected = findings[:limit] if limit else []
        return {
            "mode": "findings",
            "finding_count": len(findings),
            "findings": [item.model_dump(mode="json") for item in selected],
            "filters": {
                "status": self._optional_tool_string(arguments.get("status")),
                "category": self._optional_tool_string(arguments.get("category")),
                "severity": self._optional_tool_string(arguments.get("severity")),
                "run_id": self._optional_tool_string(arguments.get("run_id")),
                "pskill_definition_id": self._optional_tool_string(arguments.get("pskill_definition_id")),
                "limit": limit,
            },
        }

    def _read_runtime_facts(self, session: Session, *, tool_call: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        runtime_service = self._require_runtime_service()
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            agent_run = self.agent_service.get_run_model(session, tool_call.agent_run_id)
            run_id = str(agent_run.run_id or "").strip()
        if not run_id:
            raise SkillValidationError(
                "psop.runtime.read 缺少 run_id。",
                details={"required_any_of": ["arguments_summary.run_id", "agent_run.run_id"]},
            )
        snapshot_limit = self._bounded_tool_limit(arguments.get("snapshot_limit"), default=1, maximum=20)
        event_limit = self._bounded_tool_limit(
            arguments.get("event_limit", arguments.get("run_event_limit")),
            default=20,
            maximum=100,
        )
        trace_limit = self._bounded_tool_limit(
            arguments.get("trace_limit", arguments.get("run_trace_limit")),
            default=20,
            maximum=100,
        )
        run = runtime_service.get_run(session, run_id)
        snapshots = runtime_service.list_snapshots(session, run_id)
        run_events = runtime_service.list_run_events(session, run_id)
        run_traces = runtime_service.list_run_traces(session, run_id)
        selected_snapshots = snapshots[-snapshot_limit:] if snapshot_limit else []
        selected_events = run_events[-event_limit:] if event_limit else []
        selected_traces = run_traces[-trace_limit:] if trace_limit else []
        return {
            "state_source": "runtime_persisted_facts",
            "used_as_runtime_state": False,
            "run": run.model_dump(mode="json"),
            "latest_snapshot": snapshots[-1].model_dump(mode="json") if snapshots else None,
            "snapshots": [item.model_dump(mode="json") for item in selected_snapshots],
            "run_events": [item.model_dump(mode="json") for item in selected_events],
            "run_traces": [item.model_dump(mode="json") for item in selected_traces],
            "counts": {
                "snapshot_count": len(snapshots),
                "run_event_count": len(run_events),
                "run_trace_count": len(run_traces),
            },
            "limits": {
                "snapshot_limit": snapshot_limit,
                "event_limit": event_limit,
                "trace_limit": trace_limit,
            },
        }

    def _pskill_id_from_arguments(self, session: Session, arguments: dict[str, Any]) -> str:
        pskill_id = str(arguments.get("pskill_id") or arguments.get("skill_id") or "").strip()
        if pskill_id:
            return pskill_id
        pskill_key = str(arguments.get("pskill_key") or arguments.get("skill_key") or "").strip()
        if not pskill_key:
            raise SkillValidationError(
                "工具参数缺少 PSkill 标识。",
                details={"required_any_of": ["pskill_id", "skill_id", "pskill_key", "skill_key"]},
            )
        definition = self._require_pskills_service().repository.get_pskill_definition_by_key(session, pskill_key)
        if not definition:
            raise SkillNotFoundError("未找到对应的 Skill。", details={"pskill_key": pskill_key})
        return definition.id

    @staticmethod
    def _required_tool_argument(arguments: dict[str, Any], *names: str) -> str:
        for name in names:
            value = arguments.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise SkillValidationError("工具参数缺失。", details={"required_any_of": list(names), "arguments_summary": arguments})

    @staticmethod
    def _bounded_tool_limit(value: Any, *, default: int, maximum: int) -> int:
        if value is None or value == "":
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise SkillValidationError("工具 limit 参数必须是整数。", details={"value": value}) from error
        return max(0, min(parsed, maximum))

    @staticmethod
    def _optional_tool_string(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _document_from_tool_manifest(manifest: dict[str, Any]) -> SkillDocument:
        if "skill" in manifest:
            try:
                return SkillDocument.model_validate(manifest)
            except Exception as error:
                raise SkillValidationError(
                    "psop.pskill_manifest.render manifest 无效。",
                    details={"error": str(error)},
                ) from error
        return document_from_manifest_snapshot(manifest)

    def _activate_skills(self, session: Session, *, agent_run: AgentRun, spec: dict[str, Any]) -> tuple[set[str], list[str]]:
        self.skill_service.sync_packages(session)
        selected_names = list(spec.get("allowed_skill_names") or DEFAULT_AGENT_SKILLS.get(agent_run.agent_key, []))
        active_tools: set[str] = set()
        active_skill_names: list[str] = []
        for package_name in selected_names:
            package = self.skill_repository.get_package_by_name(session, package_name)
            if not package or not package.active_version_id:
                continue
            version = self.skill_repository.get_version(session, package.active_version_id)
            if not version:
                continue
            active_skill_names.append(package.name)
            active_tools.update(str(tool) for tool in version.allowed_tools)
            activation = self.skill_repository.get_activation(
                session,
                agent_run_id=agent_run.id,
                version_id=version.id,
            )
            if not activation:
                session.add(
                    SkillActivation(
                        agent_run_id=agent_run.id,
                        package_id=package.id,
                        version_id=version.id,
                        activation_context={
                            "agent_key": agent_run.agent_key,
                            "package_name": package.name,
                            "content_hash": version.content_hash,
                        },
                    )
                )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.skills.activated",
                phase="skills",
                payload={
                    "skill_names": selected_names,
                    "active_skill_names": active_skill_names,
                    "allowed_tools": sorted(active_tools),
                },
            ),
            commit=False,
        )
        return active_tools, active_skill_names

    def _handle_tool_call(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        spec: dict[str, Any],
        decision: AgentDecision,
        active_tools: set[str],
    ) -> AgentRunResponse:
        if not decision.tool_name:
            raise SkillValidationError("tool_call decision 缺少 tool_name。", details={"agent_run_id": agent_run.id})
        effective_allowed_tools = set(str(tool) for tool in spec.get("allowed_tools") or [])
        effective_allowed_tools &= active_tools
        effective_allowed_tools &= self.tool_policy.allowed_tools
        policy_decision = self.tool_policy.check(
            tool_name=decision.tool_name,
            tool_provider=decision.tool_provider,
            requested_side_effect_level=decision.side_effect_level,
            effective_allowed_tools=effective_allowed_tools,
        )
        tool_call = self.agent_service.create_tool_call(
            session,
            agent_run.id,
            CreateAgentToolCallRequest(
                tool_name=decision.tool_name,
                tool_provider=decision.tool_provider,
                arguments_summary=decision.arguments_summary,
                side_effect_level=policy_decision.side_effect_level,
                idempotency_key=decision.idempotency_key,
            ),
            commit=False,
        )
        if not policy_decision.allowed:
            model = self.agent_service.repository.get_tool_call(session, tool_call.id)
            if model:
                model.status = "blocked"
            agent_run.status = "failed"
            agent_run.error_message = policy_decision.reason
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.tool_call.blocked",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "reason": policy_decision.reason},
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        if policy_decision.requires_authorization:
            authorization_reason = decision.authorization_reason or f"{decision.tool_name} requires authorization."
            return self._request_tool_authorization(
                session,
                agent_run=agent_run,
                tool_call_id=tool_call.id,
                decision=decision,
                side_effect_level=policy_decision.side_effect_level,
                authorization_reason=authorization_reason,
            )

        model = self.agent_service.repository.get_tool_call(session, tool_call.id)
        if model:
            model.status = "executing"
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="tool.execution_started",
                phase="tool",
                payload={
                    "tool_call_id": tool_call.id,
                    "tool_name": decision.tool_name,
                    "tool_provider": decision.tool_provider,
                    "side_effect_level": policy_decision.side_effect_level,
                    "authorization_id": "",
                },
            ),
            commit=False,
        )
        try:
            tool_result = self._execute_native_tool_call(session, tool_call=model or tool_call)
        except (SkillNotFoundError, SkillValidationError) as error:
            if model:
                model.status = "failed"
                model.result_summary = {"executed": False, "error": error.message, "details": error.details}
            agent_run.status = "failed"
            agent_run.error_message = error.message
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="tool.execution_failed",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "tool_name": decision.tool_name, "error": error.message},
                ),
                commit=False,
            )
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.tool_call.failed",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "tool_name": decision.tool_name, "error": error.message},
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        if model:
            model.status = "succeeded"
            model.result_summary = {"executed": True, "policy": policy_decision.reason, **tool_result}
        agent_run.status = "succeeded"
        agent_run.output_payload = {"tool_result": {"tool_name": decision.tool_name, "status": "succeeded", **tool_result}}
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="tool.execution_succeeded",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": decision.tool_name, "result": tool_result},
            ),
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.tool_call.succeeded",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": decision.tool_name, "result": tool_result},
            ),
            commit=False,
        )
        session.commit()
        return self.agent_service._build_run_response(agent_run)

    def _request_tool_authorization(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        tool_call_id: str,
        decision: AgentDecision,
        side_effect_level: str,
        authorization_reason: str,
    ) -> AgentRunResponse:
        self.agent_service.create_tool_authorization(
            session,
            CreateToolAuthorizationRequest(
                agent_run_id=agent_run.id,
                agent_tool_call_id=tool_call_id,
                run_id=agent_run.run_id,
                tool_name=decision.tool_name,
                tool_provider=decision.tool_provider,
                side_effect_level=side_effect_level,
                risk_level="high" if side_effect_level == "high_write" else "medium",
                authorization_reason=authorization_reason,
                tool_arguments_summary=decision.arguments_summary,
                expected_effect_summary=decision.expected_effect_summary,
                reversible=decision.reversible,
                idempotency_key=decision.idempotency_key,
                request_payload={"decision": decision.model_dump(mode="json")},
            ),
        )
        return self.agent_service.get_run(session, agent_run.id)

    def _write_memory_candidates(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        output_payload: dict[str, Any],
    ) -> int:
        candidates = output_payload.get("memory_candidates") if isinstance(output_payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return 0
        entries = self.memory_service.write_candidates(
            session,
            agent_key=agent_run.agent_key,
            created_by_agent_run_id=agent_run.id,
            candidates=candidates,
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.memory_candidates.written",
                phase="memory",
                payload={"memory_entry_ids": [item.id for item in entries]},
            ),
            commit=False,
        )
        return len(entries)

    @staticmethod
    def _memory_context_limit(spec: dict[str, Any]) -> int:
        policy = spec.get("memory_policy")
        if not isinstance(policy, dict):
            return 5
        try:
            return max(1, min(20, int(policy.get("context_limit") or 5)))
        except (TypeError, ValueError):
            return 5

    @staticmethod
    def _decision_from_input(input_payload: dict[str, Any]) -> AgentDecision:
        payload = input_payload.get("agent_decision") or {"decision_type": "final_output", "output_payload": input_payload.get("expected_output", {})}
        if isinstance(payload, AgentDecision):
            return payload
        if not isinstance(payload, dict):
            raise SkillValidationError("agent_decision 必须是对象。")
        return AgentDecision(**payload)
