from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent_harness.runner import AgentRunner
from app.agents.schemas import CreateAgentRunRequest
from app.agents.service import AgentService
from app.pskills.exceptions import SkillNotFoundError, SkillSourceConflictError, SkillValidationError
from app.pskills.schemas import GeneratePSkillDraftPatchRequest, PSkillDraftGenerateResponse
from app.pskills.service import SkillsService


class PSkillDraftService:
    """Coordinates builder AgentRuns for reviewable PSkill draft patches."""

    def __init__(
        self,
        *,
        skills_service: SkillsService,
        agent_service: AgentService | None = None,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self.skills_service = skills_service
        self.agent_service = agent_service or AgentService()
        self.agent_runner = agent_runner or AgentRunner(pskills_service=skills_service)

    def generate_draft_patch(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: GeneratePSkillDraftPatchRequest,
    ) -> PSkillDraftGenerateResponse:
        detail = self.skills_service.get_skill_detail(session, skill_id)
        source = self.skills_service.get_skill_source(session, skill_id)
        if payload.base_commit_sha and payload.base_commit_sha != source.head_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": payload.base_commit_sha, "actual": source.head_commit_sha},
            )
        selected_materials = self._selected_materials(session, skill_id=skill_id, material_ids=payload.material_ids)
        proposed_files = dict(payload.proposed_files) or {
            "SKILL.md": self._build_default_skill_md_proposal(
                current_skill_md=source.skill_md_content,
                user_description=payload.user_description,
                materials=selected_materials,
            )
        }
        agent_run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="pskill.builder",
                owner_type="pskill_draft",
                owner_id=skill_id,
                input_payload={
                    "source": "pskill.draft.generate",
                    "pskill": {
                        "id": skill_id,
                        "key": detail.key,
                        "name": detail.name,
                    },
                    "user_description": payload.user_description,
                    "material_ids": [item["id"] for item in selected_materials],
                    "base_commit_sha": source.head_commit_sha,
                    "agent_decision": {
                        "decision_type": "tool_call",
                        "tool_name": "psop.repository.propose_patch",
                        "side_effect_level": "low_write",
                        "arguments_summary": {
                            "pskill_id": skill_id,
                            "base_commit_sha": source.head_commit_sha,
                            "summary": payload.user_description,
                            "files": proposed_files,
                            "current_files": {
                                "README.md": source.readme_content,
                                "SKILL.md": source.skill_md_content,
                            },
                        },
                    },
                },
            ),
            commit=False,
        )
        session.commit()
        executed_run = self.agent_runner.run_once(session, agent_run.id)
        patch = (executed_run.output_payload or {}).get("tool_result", {}).get("result", {})
        if executed_run.status != "succeeded" or not isinstance(patch, dict):
            raise SkillValidationError(
                "pskill.builder 未生成有效 draft patch。",
                details={"agent_run_id": executed_run.id, "status": executed_run.status},
            )
        return PSkillDraftGenerateResponse(
            status="patch_proposed",
            agent_run=executed_run,
            base_commit_sha=source.head_commit_sha,
            material_ids=[item["id"] for item in selected_materials],
            patch=patch,
        )

    def _selected_materials(
        self,
        session: Session,
        *,
        skill_id: str,
        material_ids: list[str],
    ) -> list[dict[str, Any]]:
        materials = [item.model_dump(mode="json") for item in self.skills_service.list_materials(session, skill_id=skill_id)]
        if not material_ids:
            return materials
        material_by_id = {str(item["id"]): item for item in materials}
        missing_ids = [material_id for material_id in material_ids if material_id not in material_by_id]
        if missing_ids:
            raise SkillNotFoundError("部分素材不存在。", details={"material_ids": missing_ids})
        return [material_by_id[material_id] for material_id in material_ids]

    @staticmethod
    def _build_default_skill_md_proposal(
        *,
        current_skill_md: str,
        user_description: str,
        materials: list[dict[str, Any]],
    ) -> str:
        material_lines = [
            f"- {item.get('name') or item.get('filename')}: {item.get('material_kind')} / {item.get('status')}"
            for item in materials
        ]
        section_lines = [
            "",
            "## Builder Draft Proposal",
            "",
            user_description.strip(),
            "",
            "### Material Evidence",
            "",
            *(material_lines or ["- No materials selected."]),
            "",
            "### Review Notes",
            "",
            "- This patch was generated as a reviewable draft by `pskill.builder`.",
        ]
        return current_skill_md.rstrip() + "\n" + "\n".join(section_lines).rstrip() + "\n"
