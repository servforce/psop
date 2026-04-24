from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.skills.exceptions import SkillValidationError


class SkillInputDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    required: bool = False
    description: str = ""


class SkillOutputDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    description: str = ""


class SkillIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    name: str
    description: str = ""


class SkillInterfaceContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    invocation_mode: str = "terminal"
    entry: str = "default"
    inputs: list[SkillInputDefinition] = Field(default_factory=list)
    outputs: list[SkillOutputDefinition] = Field(default_factory=list)


class TerminalCapability(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class LlmCapability(BaseModel):
    model_config = ConfigDict(extra="allow")

    route_key: str = "default"
    required: bool = True


class SandboxCapability(BaseModel):
    model_config = ConfigDict(extra="allow")

    required: bool = False


class SkillCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow")

    terminal: TerminalCapability = Field(default_factory=TerminalCapability)
    llm: LlmCapability = Field(default_factory=LlmCapability)
    mcp_tools: list[str] = Field(default_factory=list)
    sandbox: SandboxCapability = Field(default_factory=SandboxCapability)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_attempts: int = 0


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_llm_calls: int = 8
    max_tool_calls: int = 8


class ConcurrencyPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str = "single"


class IsolationPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    level: str = "default"


class RuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    timeout_seconds: int = 300
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)
    concurrency: ConcurrencyPolicy = Field(default_factory=ConcurrencyPolicy)
    isolation: IsolationPolicy = Field(default_factory=IsolationPolicy)


class CompileConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    formal_revision: str = "psop-eg-formal/v5"
    target: str = "eg.compile.artifact"
    validation_rules: list[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    identity: SkillIdentity
    interface_contract: SkillInterfaceContract
    capabilities: SkillCapabilities = Field(default_factory=SkillCapabilities)
    compile_config: CompileConfig = Field(default_factory=CompileConfig)
    runtime_policy: RuntimePolicy = Field(default_factory=RuntimePolicy)


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    skill: SkillManifest


def build_default_skill_document(key: str, name: str, description: str) -> SkillDocument:
    return SkillDocument(
        skill=SkillManifest(
            identity=SkillIdentity(key=key, name=name, description=description),
            interface_contract=SkillInterfaceContract(
                inputs=[
                    SkillInputDefinition(
                        name="user_input",
                        type="text",
                        required=True,
                        description="User request entered from WEB IDE.",
                    )
                ],
                outputs=[
                    SkillOutputDefinition(
                        name="final_response",
                        type="text",
                        description="Final response returned to the caller.",
                    )
                ],
            ),
        )
    )


def parse_skill_yaml(skill_yaml_content: str) -> SkillDocument:
    try:
        raw = yaml.safe_load(skill_yaml_content)
    except yaml.YAMLError as exc:
        raise SkillValidationError("`skill.yaml` 无法解析。", details={"error": str(exc)}) from exc

    if not isinstance(raw, dict):
        raise SkillValidationError("`skill.yaml` 顶层必须是对象。")

    try:
        return SkillDocument.model_validate(raw)
    except Exception as exc:  # pragma: no cover - pydantic error type is broad in runtime
        raise SkillValidationError("`skill.yaml` 不符合最小 Skill 结构定义。", details={"error": str(exc)}) from exc


def render_skill_yaml(document: SkillDocument) -> str:
    return yaml.safe_dump(
        document.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )


def build_default_readme(name: str, description: str) -> str:
    lines = [f"# {name}", "", description or "Managed by PSOP WEB IDE.", "", "## Notes", "", "- This repository is managed by PSOP."]
    return "\n".join(lines).strip() + "\n"


def build_default_skill_markdown(name: str, description: str) -> str:
    lines = [
        f"# {name}",
        "",
        "## Purpose",
        "",
        description or "Describe what this skill should accomplish.",
        "",
        "## Maintained By",
        "",
        "- PSOP WEB IDE",
    ]
    return "\n".join(lines).strip() + "\n"


def manifest_snapshot(document: SkillDocument) -> dict[str, Any]:
    return document.skill.model_dump(mode="json")


def runtime_policy_snapshot(document: SkillDocument) -> dict[str, Any]:
    return document.skill.runtime_policy.model_dump(mode="json")
