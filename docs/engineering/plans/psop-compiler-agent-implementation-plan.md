# PSOP Compiler Agent 实施计划

本文是阶段性实施计划和验收口径记录，不是长期架构事实源。`psop.compiler` 的职责、工具、Agent Skill 包、输入输出、校验与审计约束以 [PSOP Compiler Agent 详细设计](../../architecture/psop-compiler-agent-design.md) 为准；Agent Harness 总体边界以 [系统架构设计](../../architecture/system-architecture.md) 为准。

> 状态：待实施。
>
> 目标：在现有 Agent Harness MVP 底座上实现 `psop.compiler`，替代旧的 `SkillCompileAgent` 直连模型编译链路，同时保持现有 Compiler API、发布流程、RuntimeJob 进度和 EG artifact 持久化语义不变。
>
> 边界：模型只生成并提交 sandbox candidate；`validate_and_normalize_artifact()` 与 `ArtifactObject` / `EgCompileArtifact` 写入仍由 `CompilerService` 应用层完成。

## 1. 目标与验收标准

首版目标是在不改变外部 Compiler API 的前提下，让发布编译链路通过 `AgentHarnessService.invoke(agent_key="psop.compiler")` 生成 formal-v5 PSOP-EG candidate：

```text
CompilerService.process_compile_request()
  -> 读取 frozen Skill source
  -> 校验 manifest snapshot
  -> 构造 psop.compiler AgentInvocation
  -> AgentHarnessService.invoke(agent_key="psop.compiler")
  -> 读取 memory
  -> 通过 load_skill 加载 psop-compiler Skill 包入口
  -> 通过 load_skill_resource 加载 core/contract/mapping/review 包内资源
  -> 调用 psop.compiler.* read-only tools 获取 source、manifest、allowed runtime 和 domain pack
  -> 调用 psop.compiler.validate_formal_v5 执行确定性校验
  -> 调用 psop.compiler.submit_candidate 写入 sandbox outputs
  -> CompilerService 读取 candidate 并再次执行 validate_and_normalize_artifact()
  -> 校验通过后写入 ArtifactObject / EgCompileArtifact
  -> 更新 SkillCompileRequest、RuntimeJob 和 publish progress
```

最终验收命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_compiler_agent.py --fixture tests/fixtures/psop_compiler/minimal.json --scripted
```

脚本成功条件：

- `AgentResult.status == "succeeded"`。
- events 中包含 `agent.memory.read`。
- events 中包含一个 `agent.skill.loaded`：`psop-compiler`。
- events 中包含四个 `agent.skill.resource.loaded`：`core/SKILL.md`、`contract/SKILL.md`、`mapping/SKILL.md`、`review/SKILL.md`。
- events 中包含关键 tool calls：`psop.compiler.read_skill_source`、`psop.compiler.read_manifest_snapshot`、`psop.compiler.read_allowed_runtime`、`psop.compiler.read_domain_pack`、`psop.compiler.validate_formal_v5`、`psop.compiler.submit_candidate`。
- sandbox 中存在 `/mnt/psop/outputs/compiler-result.json`。
- sandbox 中存在 `/mnt/psop/outputs/eg.compile.artifact.json`。
- `compiler-result.json` 通过 compiler candidate 严格校验，包含 `artifact`、`compile_reason`、`source_map`、`diagnostics`、`repair_history`、`validator_summary`。
- `eg.compile.artifact.json` 的 `formal_revision` 为 `psop-eg-formal/v5`，并能通过 `validate_and_normalize_artifact()`。
- 现有 `/api/v1/compiler/*` 编译请求、重试、进度、events 和 artifact 读取行为不退化。

## 2. 当前基线与差异

当前仓库已经具备实现 `psop.compiler` 的基础设施：

- `backend/app/agent_harness/` 已有 service、schemas、events、sandbox、memory、skills、middlewares、LangChain runner、AgentDefinition registry 和 AgentRun 持久化骨架。
- `backend/app/agent_harness/agents/psop/builder/` 已实现可复用的 PSOP agent package 模式，包括 `agent.yaml`、`system.md`、`prompt.py`、`agent.py`。
- `backend/app/agent_harness/tools/spec.py` 已包含 tool 治理元数据字段，可直接用于 compiler tool 声明。
- `backend/app/agent_harness/tools/builtin/workspace.py` 已提供 sandbox workspace 读写工具，compiler 可复用。
- `backend/app/agent_harness/tools/policy.py` 已通过 `filter_tools_by_skill_allowed_tools()` 实现 AgentDefinition tools 与 Agent Skills allowed-tools 的交集过滤。
- `backend/app/domain/compiler/formal_v5.py` 已提供 deterministic validator：`validate_and_normalize_artifact()`。
- `backend/app/domain/compiler/service.py` 当前仍通过 `SkillCompileAgent.compile()` 调用旧 prompt pack，并在 `_compile_with_agent()` 中执行 2 次外层修复循环。
- `backend/app/domain/compiler/agent.py` 中的旧 `SkillCompileAgent` 会解析模型 JSON 并注入 prompt pack、domain pack、allowed runtime；这些规则应拆分到 Agent Skills、tools、validator 和 `CompilerService` invocation context 中。
- `backend/app/agent_harness/runners/langchain_agent_executor.py` 当前对 `psop.builder` 的必需 artifact 有特化检查，compiler 需要把此逻辑扩展为可支持多个 agent artifact contract。

需要在实施中明确处理的差异：

- `psop.compiler` 的实现目录必须遵循当前 `FileAgentDefinitionRegistry` 的 agent key segment 规则：`backend/app/agent_harness/agents/psop/compiler/`。
- 旧 `skill_compilation/formal_v5_compile/v1` prompt pack 不能直接作为新的 `system.md` 复用；其中稳定编译方法拆入 `psop-compiler` Skill 包资源，formal-v5 合法性由 `psop.compiler.validate_formal_v5` 和应用层最终 validator 保证。
- 旧 `SkillCompileAgent` 允许模型直接返回 artifact JSON；新链路必须要求模型通过 `psop.compiler.submit_candidate` 写入 sandbox candidate。
- `submit_candidate` 成功只表示 candidate 已提交，不表示可发布；ready artifact 只能由 `CompilerService` 最终校验后写入。
- 本计划首版不新增 shell、GitLab、数据库、对象存储或 MCP write tool。

额外实施约定：

- 智能体、tools、skills 中凡是可以使用中文的说明性文本必须使用简体中文，包括 `agent.yaml.description`、`ToolSpec.description`、`ToolSpec.purpose`、Agent Skill frontmatter `description`、`system.md` 和面向审阅者的错误/校验消息。
- 协议字段名、枚举值、API 路径、Python 标识符和第三方接口原文字段保留英文。
- `backend/app/agent_harness/tools/` 根目录只存放 tool 抽象、registry、policy、adapter 和公共模型；compiler 内置 tool 放在 `backend/app/agent_harness/tools/builtin/compiler.py`。

## 3. 实施阶段

### Step 1：定义 compiler candidate schema 与校验

新增 compiler candidate schema 模块，例如 `backend/app/agent_harness/agents/psop/compiler/schemas.py`。

必需常量：

```python
COMPILER_RESULT_VIRTUAL_PATH = "/mnt/psop/outputs/compiler-result.json"
COMPILER_EG_ARTIFACT_VIRTUAL_PATH = "/mnt/psop/outputs/eg.compile.artifact.json"
REQUIRED_COMPILER_CANDIDATE_FIELDS = [
    "artifact",
    "compile_reason",
    "source_map",
    "diagnostics",
    "repair_history",
    "validator_summary",
]
```

实现要求：

- 提供 `validate_compiler_candidate(candidate: Any) -> dict[str, Any]` 或等价函数，返回规范化后的 candidate，失败抛出可读 `ValueError`。
- 校验顶层必须是 object，且包含所有必需字段。
- `artifact` 必须是 object，`artifact.formal_revision` 必须为 `psop-eg-formal/v5`。
- `artifact` 至少包含 `schema`、`nodes`、`init`、`halt`、`policies`、`dependency_graph_for_view`、`runtime_contract`。
- `artifact.runtime_contract.workflow_steps` 必须是非空数组。
- `compile_reason` 必须是非空字符串。
- `source_map` 必须是非空数组；每项至少包含 `target` 和 `source_file`，并包含 `source_excerpt` 或 `source_summary`。
- `diagnostics` 和 `repair_history` 必须是数组。
- `validator_summary` 必须包含 `status`、`error_count`、`warning_count`。
- `validator_summary.status` 只接受 `passed`、`failed`、`not_run`；`not_run` 只能用于 failed candidate，不能作为 ready path。

测试：

- candidate 缺字段失败。
- `formal_revision` 错误失败。
- 缺 `runtime_contract.workflow_steps` 失败。
- `source_map` 为空失败。
- `validator_summary.status=passed` 且 `error_count>0` 失败。
- 最小合法 candidate 通过。

### Step 2：实现 compiler tools

新增 `backend/app/agent_harness/tools/builtin/compiler.py`，提供 `register_compiler_tools(registry: ToolRegistry) -> None`。

工具清单：

- `psop.compiler.read_skill_source`
- `psop.compiler.read_manifest_snapshot`
- `psop.compiler.read_allowed_runtime`
- `psop.compiler.read_domain_pack`
- `psop.compiler.validate_formal_v5`
- `psop.compiler.submit_candidate`

通用规则：

- 所有 tool 使用严格 JSON Schema，`additionalProperties=false`。
- 所有读取工具只读取 `ToolExecutionContext.invocation_context` 和 `invocation_input`，不访问 GitLab、数据库或对象存储。
- 所有 tool result 使用统一结构：`status`、`summary`、`truncated`、`next_valid_actions`，失败时包含 `type`、`message`、`retryable`。
- 大字段按 tool spec 的 `max_result_chars` 裁剪，并标记 `truncated=true`。
- 所有安全失败必须作为 tool result 返回，不让模型缺失 observation。

`psop.compiler.read_skill_source`：

- 输入：`paths?: string[]`、`max_chars?: int`。
- 从 context 读取 `source.files` 或等价 frozen source bundle。
- 返回 `source_commit_sha`、`files[path].content`、`files[path].truncated`、`source_summary`、`trust_level=frozen_source`。
- 只允许读取本次 invocation 已准备的 source path。

`psop.compiler.read_manifest_snapshot`：

- 输入：`include_runtime_policy?: boolean`。
- 从 context 读取 `manifest_snapshot`、`runtime_policy_snapshot`、`skill`。
- 返回 `skill_identity`、`compile_config`、`runtime_policy_snapshot`、`capability_summary`、`manifest_hash`、`trust_level=platform_snapshot`。

`psop.compiler.read_allowed_runtime`：

- 输入：`formal_revision?: "psop-eg-formal/v5"`。
- 返回当前 runtime 支持白名单，字段至少包含 `formal_revision`、`artifact_version`、`node_kinds`、`actors`、`tools`、`guard_ops`、`merge_ops`、`token_fields`、`policy_limits`、`unsupported_features`。
- 首版可直接来自 `backend/app/domain/compiler/formal_v5.py` 的常量和架构设计约束。

`psop.compiler.read_domain_pack`：

- 输入：`detail_level?: "metadata" | "summary" | "full"`、`max_chars?: int`。
- 从 context 读取 `domain_pack`。
- 没有 domain pack 时返回 `status=success`、`domain_pack_ref=""`、`guidance=""`，并在 `summary` 说明未配置。
- domain pack 只能作为半可信参考，返回 `trust_level=semi_trusted_reference`。

`psop.compiler.validate_formal_v5`：

- 输入：`artifact`、`validation_profile?: "mvp_runtime" | "strict_formal_v5"`、`include_normalized_summary?: boolean`。
- 调用 `validate_and_normalize_artifact()`。
- 返回 `valid`、`diagnostics`、`normalized_summary`。
- 记录 `agent.validation.started` 和 `agent.validation.completed`；失败时记录 `agent.validation.failed`。
- 不写 sandbox、数据库或对象存储。

`psop.compiler.submit_candidate`：

- 输入直接使用 compiler candidate contract。
- 调用 `validate_compiler_candidate()` 做第一层校验。
- 再调用 `validate_and_normalize_artifact(candidate["artifact"])` 形成 submission-time validation summary。
- 写入 `/mnt/psop/outputs/compiler-result.json` 和 `/mnt/psop/outputs/eg.compile.artifact.json`。
- `compiler-result.json` 保存完整 candidate；`eg.compile.artifact.json` 保存 candidate 中的 `artifact`。
- 返回 `artifact_ref`、`eg_artifact_ref`、`content_hash`、`validation_summary`。
- 写入 `agent.artifact.created` event，payload 只包含 path、hash、summary，不保存隐藏推理。

测试：

- 读取工具在缺 context 时返回结构化 error。
- 读取工具不会访问外部系统。
- validator wrapper 对合法和非法 artifact 返回正确 diagnostics。
- `submit_candidate` 对非法 candidate 返回 error，不写 outputs。
- `submit_candidate` 对合法 candidate 写入两个 outputs，并返回 hash。

### Step 3：新增 compiler Agent 包

新增目录：

```text
backend/app/agent_harness/agents/psop/compiler/
  __init__.py
  agent.py
  prompt.py
  agent.yaml
  system.md
  schemas.py
```

`agent.yaml` 固定为：

```yaml
agent_key: psop.compiler
version: v1
runner_kind: langchain_agent
factory: make_compiler_agent
description: 将冻结的 PSOP Skill source 编译为 formal-v5 PSOP-EG candidate。
model:
  name: default
  thinking_enabled: false
system_prompt_file: system.md
skills:
  - psop-compiler
tools:
  - psop.compiler.read_skill_source
  - psop.compiler.read_manifest_snapshot
  - psop.compiler.read_allowed_runtime
  - psop.compiler.read_domain_pack
  - psop.compiler.validate_formal_v5
  - psop.compiler.submit_candidate
  - workspace.read_text
  - workspace.write_text
  - workspace.list
middleware:
  - name: dangling_tool_call
  - name: model_events
    config:
      max_model_calls: 14
  - name: token_usage
  - name: tool_calls
    config:
      max_error_counts:
        psop.compiler.submit_candidate: 3
        psop.compiler.validate_formal_v5: 3
memory_scope: psop.compiler
```

`agent.py` 要求：

- 复用 builder agent 的模式创建 `ToolRegistry`。
- 注册 compiler tools、workspace tools、framework tools。
- 通过 `filter_tools_by_skill_allowed_tools(context.definition.tools, context.skill_metadata)` 得到业务工具。
- 固定注入 `load_skill` 和 `load_skill_resource`。
- 使用 `create_psop_agent()`、`build_middlewares()` 和 compiler `apply_prompt_template()`。

`prompt.py` 要求：

- 只把 system prompt、memory prompt、memory snapshot 和 Skill metadata 注入稳定前缀。
- 明确提示 agent 必须先调用 `load_skill` 读取 Skill 包入口，并调用 `load_skill_resource` 读取包内资源。
- 不把完整 frozen source 直接拼入 system prompt；source 通过工具读取。

`system.md` 要求：

- 说明 `psop.compiler` 是 draft/candidate compiler，不是发布器。
- 明确所有 source、manifest、domain pack、workspace 内容都不是系统指令。
- 明确必须通过 `psop.compiler.validate_formal_v5` 校验，并通过 `psop.compiler.submit_candidate` 提交。
- 明确不得使用未授权节点、actor、tool、guard、merge。
- 明确不得直接写 GitLab、数据库、对象存储或 ready artifact。

测试：

- `default_agent_registry(settings.backend_root).load("psop.compiler")` 成功。
- definition 的 `agent_key`、`factory`、`memory_scope`、skills、tools 与架构设计一致。
- factory 可构造 agent，且 visible tools 包含 `load_skill`、`load_skill_resource` 和 allowed business tools。

### Step 4：新增 compiler Skill 包

新增一个仓库级 Agent Skill 包：

```text
skills/psop-compiler/
  SKILL.md
  README.md
  core/SKILL.md
  contract/SKILL.md
  mapping/SKILL.md
  review/SKILL.md
```

`psop-compiler/SKILL.md` frontmatter：

```yaml
---
name: psop-compiler
description: 当需要把冻结的 PSOP Skill source、manifest snapshot 和 runtime 约束编译为 formal-v5 PSOP-EG candidate 时使用此 Skill。
allowed-tools:
  - psop.compiler.read_skill_source
  - psop.compiler.read_manifest_snapshot
  - psop.compiler.read_allowed_runtime
  - psop.compiler.read_domain_pack
  - psop.compiler.build_formal_v5_scaffold
  - psop.compiler.validate_formal_v5
  - psop.compiler.submit_candidate
  - workspace.read_text
  - workspace.write_text
  - workspace.list
---
```

根 `SKILL.md` 核心内容：

- 声明 compiler 不是 builder、publisher 或 runtime executor。
- 声明行业标准检索不是 compiler 职责；标准引用必须已固化在 frozen source、manifest 或 invocation context。
- 要求通过 `load_skill_resource` 加载 `core/SKILL.md`、`contract/SKILL.md`、`mapping/SKILL.md` 和 `review/SKILL.md`。
- 声明 `allowed-tools` 只包含业务工具；`load_skill` / `load_skill_resource` 由 framework 注入。

`core/SKILL.md` 核心内容：

- 抽取 Skill source 中的 execution goal、applicability、workflow steps、expected evidence、safety constraints、wait checkpoints、completion criteria、recovery paths。
- 保持 source 与 runtime_contract 语义同构。
- 区分 frozen source 事实、domain pack 辅助知识和 compiler inference。
- 禁止编造 source evidence，禁止用通用壳掩盖真实 workflow 缺失。

`contract/SKILL.md` 核心内容：

- formal-v5 顶层字段、runtime_contract、node、guard、merge 和 wait checkpoint 不变量。
- workflow step 输入格式与 `build_formal_v5_scaffold` 首选生成路径。
- validator diagnostics 到修复动作的映射。

`mapping/SKILL.md` 核心内容：

- 每个业务 step 映射为 `instruct_<step_id>` 和 `evaluate_<step_id>`。
- `start` 只初始化 token，不承载业务判断。
- `evaluate` 和 `final_verify` 必须能看到必要 token 投影。
- dependency graph 只表达 artifact 中真实可达边。
- policies 不得使用固定小 LLM 调用上限。

`review/SKILL.md` 核心内容：

- 自检 artifact 必需字段、start/terminal 节点、workflow step 对应节点、source_map 覆盖、validator warning 归档。
- 禁止为通过 validator 删除真实业务步骤。
- 禁止把 domain pack 当作 formal-v5 或 runtime policy 事实源。

测试：

- `SkillLoader.load_metadata()` 能读取 `psop-compiler`。
- `SkillLoader.load_resource()` 能读取 `core/SKILL.md`、`contract/SKILL.md`、`mapping/SKILL.md` 和 `review/SKILL.md`。
- `psop-compiler.allowed-tools` 不会超出 AgentDefinition tools。
- scripted run 中包含一个 `agent.skill.loaded` 事件和四个必需 `agent.skill.resource.loaded` 事件。

### Step 5：扩展必需 artifact 收集与失败判定

修改 `backend/app/agent_harness/runners/langchain_agent_executor.py`。

目标：

- 移除只针对 `psop.builder` 的硬编码判定，改为 agent artifact contract 映射。

建议结构：

```python
REQUIRED_ARTIFACTS_BY_AGENT = {
    "psop.builder": {
        "artifact_type": "skill_draft_candidate",
        "artifact_ref": "sandbox://outputs/builder-result.json",
        "virtual_path": "/mnt/psop/outputs/builder-result.json",
        "continuation_prompt": "...",
        "max_continuations": 2,
    },
    "psop.compiler": {
        "artifact_type": "eg_compile_candidate",
        "artifact_ref": "sandbox://outputs/compiler-result.json",
        "virtual_path": "/mnt/psop/outputs/compiler-result.json",
        "continuation_prompt": "...",
        "max_continuations": 2,
    },
}
```

实现要求：

- `_collect_artifacts()` 识别 `/mnt/psop/outputs/compiler-result.json`，生成 `AgentArtifact(artifact_type="eg_compile_candidate", path="sandbox://outputs/compiler-result.json")`。
- 如果 `/mnt/psop/outputs/eg.compile.artifact.json` 存在，生成 `AgentArtifact(artifact_type="eg_compile_artifact_candidate", path="sandbox://outputs/eg.compile.artifact.json")`。
- provenance 至少包含 `content_hash`；如读取 JSON 成功，可附加 `formal_revision`、`node_count`、`workflow_step_count`。
- 对有必需 artifact contract 的 agent，缺失 artifact 时追加一次 continuation prompt。
- 最终仍缺失时返回 `AgentResult.status="failed"`，`error_message` 指出缺失的 artifact ref。
- 保持 `psop.builder` 现有测试行为不变。

测试：

- builder 缺 candidate 仍失败。
- compiler 缺 candidate 失败。
- compiler 生成 `compiler-result.json` 后成功收集 `eg_compile_candidate`。
- compiler 同时生成 `eg.compile.artifact.json` 后成功收集 `eg_compile_artifact_candidate`。

### Step 6：新增 scripted compiler model、fixture 与验收脚本

新增 `backend/app/agent_harness/models/scripted_compiler_chat_model.py`。

行为要求：

- 调用 `load_skill` 加载 `psop-compiler` Skill 包入口。
- 调用 `load_skill_resource` 加载 `core/SKILL.md`、`contract/SKILL.md`、`mapping/SKILL.md` 和 `review/SKILL.md`。
- 调用 `psop.compiler.read_skill_source`、`psop.compiler.read_manifest_snapshot`、`psop.compiler.read_allowed_runtime`、`psop.compiler.read_domain_pack`。
- 构造最小合法 formal-v5 artifact。
- 调用 `psop.compiler.validate_formal_v5`。
- 调用 `psop.compiler.submit_candidate`。
- 最后返回简短最终消息。

新增 fixture：

```text
tests/fixtures/psop_compiler/minimal.json
```

fixture 必须包含：

- `agent_key: psop.compiler`
- `input.text`
- `context.compile_request`
- `context.skill`
- `context.source`
- `context.manifest_snapshot`
- `context.allowed_runtime`
- `context.domain_pack`
- `context.repair_diagnostics`

新增脚本：

```text
tests/run_psop_compiler_agent.py
```

脚本要求：

- 支持 `--fixture`。
- 支持 `--scripted`，默认真实模型可用于人工验收，但不得进入默认 pytest 强依赖。
- 输出 `agent_run_id`、`sandbox_path`、`events_path`、candidate refs。
- 校验必需 skills、tool calls、artifacts 和最终 validator 结果。

测试：

- 新增 `tests/test_agent_harness_compiler.py` 覆盖 scripted compiler run。
- 验证脚本 fixture 可以被 `AgentInvocation.model_validate()` 或等价方式加载。

### Step 7：CompilerService 接入 AgentHarnessService

修改 `backend/app/domain/compiler/service.py`。

构造函数调整：

- 新增可选参数 `agent_harness_service: AgentHarnessService | None = None`。
- 保留 `compile_agent: SkillCompileAgent | None = None` 作为测试兼容或临时 fallback，但生产路径优先使用 `agent_harness_service`。
- PSOP 应用初始化时始终构造 `AgentHarnessService`；`CompilerService` 收到 `agent_harness_service` 时使用 `psop.compiler`。
- 如需保留兼容 fallback，应明确只用于测试显式注入旧 `compile_agent` 且不传 `agent_harness_service` 的场景。

`_compile_with_agent()` 调整：

- 保留现有 2 次外层 attempt。
- 每次 attempt 构造 `AgentInvocation(agent_key="psop.compiler", input=..., context=..., memory_scope="psop.compiler", workspace_id=compile_request.id)`。
- `input.text` 只放任务摘要和必需输出说明。
- `context` 放完整结构化事实：
  - `compile_request`
  - `skill`
  - `source`
  - `manifest_snapshot`
  - `runtime_policy_snapshot`
  - `allowed_runtime`
  - `domain_pack`
  - `repair_diagnostics`
  - `output_contract`
- 调用 `AgentHarnessService.invoke()` 时传入 `persistence_session=session`。
- `persistence_context` 至少包含 `related_skill_definition_id`、`related_job_id`；如当前 persistence schema 支持 compile request，应同时记录 `compile_request_id` 或放入 input summary。
- 从 `AgentResult.artifacts` 或 `sandbox_path` 读取 `compiler-result.json`。
- 解析 candidate，调用 `validate_compiler_candidate()`。
- 取 `candidate["artifact"]` 再调用 `validate_and_normalize_artifact()`。
- 校验通过后返回 normalized artifact 和 diagnostics。
- 校验失败时把 validator diagnostics 作为下一轮 `repair_diagnostics`。

诊断兼容：

- `candidate["diagnostics"]` 转换为 `FormalDiagnostic` 后加入现有 diagnostics。
- `AgentResult.status="failed"` 时写入 `compile.agent.failed` diagnostic。
- 缺 `compiler-result.json` 时写入 `compile.agent.missing_artifact` diagnostic。
- candidate JSON 无法解析时写入 `compile.agent.invalid_json` diagnostic。
- 第二次 attempt 仍失败时保留现有 `compile.agent.repair_failed` 语义。

用量与进度：

- token usage 继续通过 AgentEvent 或 AgentRun persistence 记录。
- 如仍需要 `RuntimeJob` 累加 usage，可从 `AgentResult.events` 中提取 `agent.token.usage`。
- 保持现有 progress stage：`agent_compiling`、`artifact_validating`、`artifact_emitting`。

测试：

- 现有成功编译测试继续通过。
- 现有 repair once 测试改为通过 scripted harness 触发一次失败后修复。
- 现有 repair still invalid 测试继续失败并写 diagnostics。
- 现有 domain pack fallback 测试继续验证 compiler metadata 或 diagnostics 中的 domain pack 信息。

### Step 8：持久化与审计关联

目标：

- `psop.compiler` 的 AgentRun / AgentEvent / AgentArtifact 进入可审计链路。
- CompilerService 的 request/job/artifact 能关联 agent run。

实现要求：

- `AgentHarnessService.invoke()` 已支持 `persistence_session` 和 `persistence_context`，compiler 接入时必须使用。
- `AgentEvent` payload 只保存摘要、ID、hash、diagnostic count，不保存隐藏推理和完整大 artifact。
- `AgentArtifact` 中 `eg_compile_candidate` 记录 `compiler-result.json` 的 hash 和 path。
- `AgentArtifact` 中 `eg_compile_artifact_candidate` 记录 `eg.compile.artifact.json` 的 hash 和 path。
- 平台最终写入 `EgCompileArtifact` 后，若当前 persistence service 支持追加 artifact record，则记录 `artifact_type=eg_compile_artifact_ref`，包含 `EgCompileArtifact.id`、`ArtifactObject.id`、checksum 和 `status=ready`。
- `RuntimeJob` 或 compile progress payload 中记录 `agent_run_id`、`sandbox_path`、candidate refs 和 validator summary。

测试：

- 调用 compiler 后可通过 AgentRun query API 查到 `agent_key=psop.compiler` 的 run。
- timeline 中包含关键 tool events 和 validation events。
- artifact refs 不包含完整 hidden reasoning 或超大 artifact JSON。

### Step 9：迁移旧 prompt pack 规则并收敛兼容层

目标：

- 旧 prompt pack 不再作为生产 compiler system prompt。
- 旧测试和 API 逐步切到 harness 语义。

实施要求：

- 将旧 `SkillCompileAgent._user_prompt()` 中仍有价值的 workflow contract、allowed runtime、domain pack rule、runtime language rule 迁入 compiler Agent Skills 或 `read_allowed_runtime` tool result。
- 旧 prompt pack 文件可以保留为历史兼容，但新增注释或文档说明不再是 `psop.compiler` 的事实源。
- `CompilerService` 中旧 `SkillCompileAgent` fallback 必须有明确触发条件，不应在 agent harness 启用时静默使用。
- 不删除与当前测试仍相关的旧类；等 harness compiler 全量验证后再做清理计划。

测试：

- agent harness 启用时，CompilerService 走 `psop.compiler`。
- agent harness 关闭或测试显式注入旧 `compile_agent` 时，兼容路径仍可运行。

## 4. Public Interfaces

### AgentDefinition

新增 agent key：

```text
psop.compiler
```

实现目录：

```text
backend/app/agent_harness/agents/psop/compiler/
```

### Compiler tools

新增 business tools：

```text
psop.compiler.read_skill_source
psop.compiler.read_manifest_snapshot
psop.compiler.read_allowed_runtime
psop.compiler.read_domain_pack
psop.compiler.validate_formal_v5
psop.compiler.submit_candidate
```

工具只对 `psop.compiler` 可见，并受 AgentDefinition tools 与 Agent Skills allowed-tools 交集约束。

### Compiler candidate

sandbox candidate：

```text
sandbox://outputs/compiler-result.json
```

结构：

```json
{
  "artifact": {},
  "compile_reason": "...",
  "source_map": [],
  "diagnostics": [],
  "repair_history": [],
  "validator_summary": {
    "status": "passed",
    "error_count": 0,
    "warning_count": 0
  }
}
```

EG artifact candidate：

```text
sandbox://outputs/eg.compile.artifact.json
```

内容为 `compiler-result.json.artifact`。

### AgentInvocation.context

`CompilerService` 传入的 context 最少包含：

```json
{
  "compile_request": {},
  "skill": {},
  "source": {},
  "manifest_snapshot": {},
  "runtime_policy_snapshot": {},
  "allowed_runtime": {},
  "domain_pack": {},
  "repair_diagnostics": [],
  "output_contract": {}
}
```

context 是工具读取的事实边界。模型不得直接访问数据库、GitLab、对象存储或外部网络获取这些事实。

### AgentArtifact

新增 artifact types：

```text
eg_compile_candidate
eg_compile_artifact_candidate
eg_compile_validation_report
eg_compile_artifact_ref
```

首版必须实现前两个；后两个可随 persistence 接入补齐。

### CompilerService

新增可注入依赖：

```python
agent_harness_service: AgentHarnessService | None = None
```

外部 API response schema 不新增必需字段。若需要暴露 agent run timeline，应通过现有 AgentRun API 或后续 API 设计追加，不在本计划首版修改 Compiler API 契约。

## 5. 测试矩阵

| 层级 | 文件 | 覆盖点 |
| --- | --- | --- |
| schema | `tests/test_agent_harness_compiler.py` | compiler candidate 必需字段、formal revision、source map、validator summary。 |
| tools | `tests/test_agent_harness_compiler.py` | compiler read tools、validator tool、submit candidate 写 outputs。 |
| definition | `tests/test_agent_harness_compiler.py` | `psop.compiler` AgentDefinition、factory、skills metadata、allowed-tools 过滤。 |
| runner | `tests/test_agent_harness_runner.py` | 必需 artifact contract 泛化，builder 行为不退化，compiler 缺 candidate 失败。 |
| harness e2e | `tests/test_agent_harness_compiler.py` | scripted compiler run 加载 skills、调用 tools、写 artifacts。 |
| service | `tests/test_runtime_services.py` | CompilerService 通过 harness 生成 artifact、repair once、repair failed、domain pack fallback。 |
| API | `tests/test_skills_api.py` | compiler requests、retry、progress、events、artifact 读取不退化。 |
| persistence | `tests/test_agent_harness_persistence.py` 或新增测试 | AgentRun / AgentEvent / AgentArtifact 可查询，payload 安全裁剪。 |

默认测试不得依赖真实 LLM。真实模型脚本只作为人工验收命令。

脚本验收：

```bash
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_compiler_agent.py --fixture tests/fixtures/psop_compiler/minimal.json --scripted
```

全量验收：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
```

## 6. 交付拆分建议

建议按以下提交拆分，减少回归面：

1. compiler candidate schema + compiler tools + 工具单测。
2. compiler Agent package + three Agent Skills + definition/factory 单测。
3. runner artifact contract 泛化 + builder 回归测试 + compiler artifact 收集测试。
4. scripted compiler model + fixture + run script + harness e2e 测试。
5. CompilerService 接入 AgentHarnessService + runtime service compiler 测试迁移。
6. persistence/timeline 关联 + API 回归测试。
7. 旧 prompt pack 规则收敛、文档补充和最终全量验收。

每个提交都应保持 `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q` 可通过，除非提交说明明确标记为中间不可独立发布状态；推荐避免中间不可发布提交。

## 7. 风险与约束

- Prompt injection：Skill source、domain pack、workspace 文件、validator diagnostics 都只能作为数据事实或工具 observation，不能覆盖 system prompt、Agent Skill 或工具权限。
- Validator 绕过：模型不得自然语言声明 candidate 合法；只有 `psop.compiler.validate_formal_v5` 和应用层 `validate_and_normalize_artifact()` 的结果可信。
- Artifact 缺失：runner 必须把缺少 `compiler-result.json` 判为 failed，不能让自然语言 final answer 伪装成功。
- Artifact 双写：`submit_candidate` 只写 sandbox candidate；ready artifact 只由 `CompilerService` 写数据库对象和 `EgCompileArtifact`。
- 旧链路兼容：旧 `SkillCompileAgent` 可以暂存为 fallback 或测试替身，但 agent harness 启用时不得静默走旧生产链路。
- Token 超限：完整 source、domain guidance、validator diagnostics 和 artifact 应由 tools 裁剪或保存为 artifact ref，不应全部塞入 prompt。
- Source traceability：`source_map` 不足时应失败或记录 blocking diagnostics，不得编造 source evidence。
- Runtime 兼容：allowed runtime 是硬边界；domain pack 不能扩展 node kind、actor、tool、guard 或 merge 白名单。
- Persistence 安全：AgentEvent / AgentArtifact payload 只记录摘要、hash、ID 和安全字段，不保存隐藏推理或完整大 artifact。

## 8. 完成定义

本计划完成必须同时满足：

- `backend/app/agent_harness/agents/psop/compiler/agent.yaml` 可被 `default_agent_registry()` 加载。
- `psop-compiler` Skill 包可被 `SkillLoader.load_metadata()` 和 `load_skill` 读取。
- `psop-compiler` 包内 core、contract、mapping、review 资源可被 `SkillLoader.load_resource()` 和 `load_skill_resource` 读取。
- `psop.compiler` scripted run 产生 `compiler-result.json` 和 `eg.compile.artifact.json`。
- `compiler-result.json` 通过 compiler candidate schema 校验。
- `eg.compile.artifact.json` 通过 `validate_and_normalize_artifact()`。
- `LangChainAgentExecutor` 对 `psop.builder` 和 `psop.compiler` 的必需 artifact 都能正确判定。
- `CompilerService` 在 agent harness 启用时通过 `psop.compiler` 编译，并保留现有 2 次修复语义。
- 编译成功后仍由应用层写入 `ArtifactObject` / `EgCompileArtifact`，外部 Compiler API 行为不变。
- 编译失败时写入 `CompileDiagnostic`，并保持 `SkillCompileRequest` / `RuntimeJob` 失败状态语义。
- 默认 pytest 不依赖真实 LLM。
- 以下命令通过：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_compiler_agent.py --fixture tests/fixtures/psop_compiler/minimal.json --scripted
```
