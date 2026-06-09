from __future__ import annotations

from typing import Any


PSOP_GOVERNANCE_SPEC: dict[str, Any] = {
    "key": "psop.governance",
    "name": "PSOP Governance",
    "role": "governance",
    "goal": "将评估结果转为可验证、可审批、可回滚的系统改进提案。",
    "usage_keys": ["psop.governance.proposal"],
    "allowed_tools": [
        "psop.evaluations.read",
        "psop.governance.write_proposal",
        "psop.agent_version.activate",
        "psop.skill_version.activate",
    ],
    "allowed_skill_names": ["psop-governance-manager"],
    "output_schema": {"name": "GovernanceProposalResult"},
}
