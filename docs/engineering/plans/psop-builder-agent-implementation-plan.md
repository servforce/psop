# PSOP Builder Agent 实施计划（已实施，验收口径已同步）

本文是阶段性实施计划和当前验收口径记录，不是长期架构事实源。`psop.builder` 的职责、工具、Agent Skills、输入输出、校验与审计约束以 [PSOP Builder Agent 详细设计](../../architecture/psop-builder-agent-design.md) 为准；Agent Harness 总体边界以 [系统架构设计](../../architecture/system-architecture.md) 为准。

## 1. 目标与验收标准

目标是在现有 Agent Harness MVP 底座上实现 `psop.builder`，替代旧的 `skill_creation.conversational_draft` 直连模型生成链路。首版验收不是完整生产闭环，而是能通过一个 Python 脚本真实调用 `psop.builder` 完成一次 agent run：

```text
AgentHarnessService.invoke(agent_key="psop.builder")
  -> 读取 memory
  -> 通过 load_skill 加载三个 builder Agent Skills
  -> 调用 psop.builder.* read-only tools 获取 source、素材分析和参考资产
  -> 调用 psop.standard.search 尝试检索行业标准
  -> 调用 workspace tools 产生可审阅中间产物
  -> 调用 psop.builder.submit_candidate 写入 outputs/builder-result.json 和 outputs/skill-draft/*.md
  -> 返回 AgentResult，并产生可审计 AgentEvent
```

最终验收命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_builder_agent.py --fixture tests/fixtures/psop_builder/minimal.json
```

脚本成功条件：

- `AgentResult.status == "succeeded"`。
- events 中包含 `agent.memory.read`。
- events 中包含三个 `agent.skill.loaded`：`psop-builder-core`、`psop-builder-evidence-mapping`、`psop-builder-quality-review`。
- events 中包含关键 tool calls：`psop.builder.read_current_source`、`psop.builder.list_materials`、`psop.builder.read_material_analysis`、`psop.builder.list_reference_assets`、`psop.standard.search`、`psop.builder.submit_candidate`。
- sandbox 中存在 `/mnt/psop/outputs/builder-result.json`。
- sandbox 中存在 `/mnt/psop/outputs/skill-draft/`，且所有 candidate 必需文件已按相对路径物化为非空 Markdown 文件。最终可提交的 PSOP Skill 文件以 `outputs/skill-draft/` 为准，不要求与 `builder-result.json.files` 字节级一致。
- 如 candidate 选择了图片参考资产，物化后的 `SKILL.md` 必须在使用该图片的流程步骤中通过相对 Markdown 图片链接展示，不得使用 base64 data URI，也不得把参考图片集中追加到文档底部。
- `builder-result.json` 通过 builder v1 candidate 严格校验，且包含所有必需文件和追溯字段。

## 2. 当前基线与差异

当前仓库已经完成 Agent Harness MVP demo：

- `backend/app/agent_harness/` 已有 service、schemas、events、sandbox、memory、tools、skills、middlewares、LangChain runner 和 demo agent。
- `tests/run_agent_demo.py` 已能调用 `demo.psop_harness_agent`，并覆盖 skill、tools、memory、workspace、events。
- `SkillsService.generate_skill_draft_from_raw_materials()` 仍使用旧 Prompt Pack `skill_creation/conversational_draft/v1`，通过 `LlmInferenceGateway.complete()` 生成 JSON，再调用 `parse_generated_skill_draft()`、`_resolve_selected_reference_assets()` 和 `_commit_generated_skill_files()`。
- 当前 `parse_generated_skill_draft()` 只做兼容解析和基础文件校验，尚未覆盖 builder 设计要求的 evidence map、行业标准引用、workflow step、expected evidence 和 safety constraints。

需要在实施中明确处理两处文档/代码差异：

- `system-architecture.md` 中早期目录示例写过 `backend/app/agent_harness/agents/builder/`，但当前 `FileAgentDefinitionRegistry` 以 agent key segment 映射目录，且 builder 详细设计要求 `psop.builder`，因此实现目录应为 `backend/app/agent_harness/agents/psop/builder/`。
- 实施前 `ToolSpec` 只有 `name`、`description`、`input_schema`、`output_schema`、`source`，还没有 builder 设计要求的 `risk_class`、`side_effect_class`、`resource_scope`、`permission_policy`、`timeout_seconds`、`max_result_chars`、`retry_policy`、`audit_event`、`error_types` 等治理元数据。本计划的 builder 验收只要求这些字段完成建模并供工具声明使用；统一运行时强制策略属于 Agent Harness tools 领域的后续增强，不作为 builder agent 首版完成阻塞。

额外实施约定：

- 智能体、tools、skills 中凡是可以使用中文的说明性文本必须使用简体中文，包括 `agent.yaml.description`、`ToolSpec.description`、`ToolSpec.purpose`、Agent Skill frontmatter `description`、`system.md` 和面向审阅者的错误/校验消息。协议字段名、枚举值、API 路径、Python 标识符和第三方接口原文字段保留英文。
- `backend/app/agent_harness/tools/` 根目录只存放 tool 相关抽象、registry、policy、adapter 和公共模型；具体内置 tool 实现统一放在 `backend/app/agent_harness/tools/builtin/` 目录下。已有 `tools/builtin.py` 应迁移为兼容 wrapper 或拆入 `tools/builtin/demo.py`，避免继续在根目录堆放具体 tool handler。

## 3. 实施阶段

### Step 1：补齐 ToolSpec 治理元数据

修改 `backend/app/agent_harness/tools/spec.py`：

- 为 `ToolSpec` 增加治理字段：
  - `purpose: str | None`
  - `risk_class: str = "read_only"`
  - `side_effect_class: str = "none"`
  - `resource_scope: str = "agent_run"`
  - `permission_policy: str = "allow"`
  - `timeout_seconds: float | None = None`
  - `max_result_chars: int | None = None`
  - `retry_policy: dict[str, Any] = {}`
  - `audit_event: str | None = None`
  - `error_types: list[str] = []`
- 保持现有 demo tools 不需要立即补全所有字段，依赖默认值继续通过测试。
- `ToolRegistry.openai_tools()` 仍只暴露模型需要的 name、description、parameters；治理字段由 Harness 和 handler 使用，不作为 prompt 唯一安全机制。

测试：

- 新增或扩展 `tests/test_agent_harness_tools.py`，断言 `ToolSpec` 默认治理字段存在且 demo tools 兼容。

### Step 2：整理内置 tools 目录并新增通用 workspace tools

先建立内置 tools 包：

```text
backend/app/agent_harness/tools/builtin/
  __init__.py
  demo.py
  workspace.py
  builder.py
  standard.py
```

迁移规则：

- `backend/app/agent_harness/tools/` 根目录保留 `spec.py`、`registry.py`、`policy.py`、`framework.py`、`langchain.py`、`mcp_provider.py` 等抽象或适配层。
- 现有 `backend/app/agent_harness/tools/builtin.py` 中 demo tool 迁移到 `tools/builtin/demo.py`。
- 如为兼容旧 import 保留 `tools/builtin.py`，该文件只能 re-export `tools.builtin.demo.register_builtin_tools`，不得继续放具体 handler。

新增 `backend/app/agent_harness/tools/builtin/workspace.py`：

- 注册：
  - `workspace.list`
  - `workspace.read_text`
  - `workspace.write_text`
- 只允许访问 `/mnt/psop/workspace`。
- 拒绝绝对 host path、`..`、空路径、NUL 字符、越界符号链接语义。
- `workspace.write_text` 不允许写 `/mnt/psop/outputs/builder-result.json`，避免绕过 `psop.builder.submit_candidate`。
- 输出统一包含 `status`、`virtual_path`、`bytes`、`truncated`、`artifact_ref` 或结构化 error。

测试：

- path escape 被拒绝。
- 写入和读取 sandbox workspace 成功。
- `workspace.list` 对不存在目录返回结构化 error，不抛出未审计异常。

### Step 3：定义 builder candidate schema 与严格校验

新增 `backend/app/agent_harness/agents/psop/builder/schemas.py`：

- 定义 `BuilderCandidate` Pydantic schema，对齐设计文档输出：
  - `directory_tree`
  - `files`
  - `generation_reason`
  - `review_notes`
  - `material_usage`
  - `industry_standard_usage`
  - `selected_reference_assets`
  - `evidence_map`
  - `missing_questions`
  - `safety_constraints`
  - `workflow_step_candidates`
  - `expected_evidence_requirements`
- 校验规则：
  - `files` 必须包含 `README.md`、`SKILL.md`、`prompts/system.md`、`references/README.md`、`examples/input.md`、`examples/expected-output.md`、`tests/checklist.md`。
  - `files` 不得包含 `skill.yaml`、绝对路径、`..` 或空内容。
  - `material_usage` 非空，每项至少包含 `material_id` 和 `usage`。
  - `selected_reference_assets` 数量为 1 到 `MAX_SKILL_REFERENCE_ASSETS`，且必须来自 `candidate_reference_assets`。
  - `industry_standard_usage` 字段必须存在；使用标准时必须引用 `psop.standard.search` 返回的可追溯结果。
  - 如果 `psop.standard.search` 结果无法抽取非空 `standard_ref` 和 `clause_ref`，该结果只能作为 `reference_only` 或进入 `review_notes`，不得被写成强制行业标准约束。
  - `evidence_map.source_refs[].source_type` 只能使用设计文档允许的来源类型。
  - `safety_constraints`、`workflow_step_candidates`、`expected_evidence_requirements` 非空，并能与 `SKILL.md` 阶段编号或标题对应。
  - `human_confirmation_required` 必须同步出现在 `missing_questions` 或 `review_notes`。
- 保留 `parse_generated_skill_draft()` 作为旧链路兼容解析，不在本步骤替换旧解析器行为。

测试：

- 最小合法 candidate 通过。
- 缺必需文件、包含 `skill.yaml`、非法路径、空 `material_usage`、伪造 asset id、伪造 standard ref、非法 evidence source 均失败并返回可读错误。

### Step 4：实现 builder tools

新增 `backend/app/agent_harness/tools/builtin/builder.py`：

- `register_builder_tools(registry)` 注册：
  - `psop.builder.read_current_source`
  - `psop.builder.list_materials`
  - `psop.builder.read_material_analysis`
  - `psop.builder.list_reference_assets`
  - `psop.builder.submit_candidate`
- read-only tools 只读取 `AgentInvocation.input` 和 `AgentInvocation.context`，不直接访问数据库、GitLab 或对象存储。
- `read_current_source` 返回当前 source、`source_ref`、`source_commit_sha` 和 `trust_level=current_source`。
- `list_materials` 返回素材 id、kind、filename、analysis id、status、summary、artifact ref，不返回完整 OCR/ASR 大段文本。
- `read_material_analysis` 按 `material_id` 返回裁剪后的结构化 evidence、actions、states、risks、uncertainties。
- `list_reference_assets` 返回候选资产 id、material id、kind、reference path、timestamp、observation summary、suggested use、confidence。
- `submit_candidate` 执行 Step 3 的严格校验，写入 sandbox `/mnt/psop/outputs/builder-result.json`，并将 `files` 物化到 `/mnt/psop/outputs/skill-draft/`，记录 `skill_draft_candidate` 和 `skill_draft_files` 两类 `agent.artifact.created`。

工具统一结果：

```json
{
  "status": "success",
  "summary": "...",
  "items": [],
  "artifact_ref": null,
  "truncated": false,
  "next_valid_actions": []
}
```

错误统一结果：

```json
{
  "status": "error",
  "type": "invalid_arguments",
  "message": "...",
  "retryable": false,
  "next_valid_actions": []
}
```

测试：

- read tools 从 fixture context 返回预期裁剪结果。
- `submit_candidate` 成功时写入 outputs artifact。
- `submit_candidate` 失败时返回结构化 error，且不写入 artifact。

### Step 5：实现 LightRAG 标准检索工具

新增配置到 `backend/app/core/config.py`：

```python
standard_lightrag_base_url: str = "http://10.0.0.20:9621"
standard_lightrag_api_key: str = "servforce"
standard_lightrag_timeout_seconds: float = 20.0
standard_lightrag_max_results: int = 8
```

同步更新 `.env.example`，增加：

```text
PSOP_STANDARD_LIGHTRAG_BASE_URL=http://10.0.0.20:9621
PSOP_STANDARD_LIGHTRAG_API_KEY=servforce
PSOP_STANDARD_LIGHTRAG_TIMEOUT_SECONDS=20
PSOP_STANDARD_LIGHTRAG_MAX_RESULTS=8
```

新增 `backend/app/agent_harness/tools/builtin/standard.py`：

- 注册 `psop.standard.search`。
- 只允许调用配置的 LightRAG HTTP 服务，不暴露 URL、token 或任意 HTTP tool 给模型。
- 本次接入的 LightRAG 接口文档为 `http://10.0.0.20:9621/openapi.json`，工具只使用 `PSOP /query` 对应的 `POST /query` 非流式接口，不使用 `/query/stream`、`/query/data` 或 documents 写接口。
- 请求 header 必须包含 `X-API-Key`，值来自 `standard_lightrag_api_key`，本地/当前环境默认值为 `servforce`。
- 输入 schema 对齐 builder 详细设计：`query` 必填，支持 `task_summary`、`jurisdiction`、`standard_scope`、`hazard_types`、`equipment_keywords`、`max_results`。
- handler 将 PSOP 内部输入映射为 LightRAG `QueryRequest`：

```json
{
  "query": "...",
  "mode": "mix",
  "include_references": true,
  "include_chunk_content": true,
  "stream": false,
  "response_type": "Bullet Points",
  "top_k": 8,
  "chunk_top_k": 8,
  "max_total_tokens": 6000
}
```

- LightRAG `QueryResponse` 只保证包含 `response` 和可选 `references[]`；每个 reference 至少包含 `reference_id`、`file_path`，当 `include_chunk_content=true` 时可能包含 `content[]`。
- handler 必须把 LightRAG 响应规范化为 PSOP 内部 observation，最多 `standard_lightrag_max_results` 条，每条 snippet 不超过 1200 字。
- `standard_ref`、`title`、`clause_ref` 从 `response`、`references[].file_path` 和 `references[].content[]` 中保守抽取；无法可靠抽取时不得伪造，返回 `citation_status="incomplete"`，由 builder 写入 `review_notes` 或 `industry_standard_usage[].usage="reference_only"`。
- 缺配置、超时、鉴权失败、非 2xx、响应结构异常都返回结构化 error observation，不抛出未捕获异常。
- 成功和失败都记录安全摘要事件；事件 payload 不保存完整标准原文。

测试：

- 使用 mocked HTTP transport 覆盖 success、timeout、service unavailable、malformed response。
- max results 和 snippet 长度被限制。
- 断言请求使用 `POST /query`，header 包含 `X-API-Key: servforce`。
- 当 LightRAG 返回 reference 但无法抽取条款号时，工具返回 `citation_status="incomplete"`，candidate 校验不允许把它当作已采纳标准。

### Step 6：新增 builder Agent 包

新增目录：

```text
backend/app/agent_harness/agents/psop/
  __init__.py
  builder/
    __init__.py
    agent.py
    prompt.py
    agent.yaml
    system.md
```

`agent.yaml`：

```yaml
agent_key: psop.builder
version: v1
runner_kind: langchain_agent
factory: make_builder_agent
description: 根据用户目标、当前 Skill source、素材分析结果和行业标准引用构建可审阅的 PSOP Skill draft candidate。
model:
  name: default
  thinking_enabled: false
system_prompt_file: system.md
skills:
  - psop-builder-core
  - psop-builder-evidence-mapping
  - psop-builder-quality-review
tools:
  - psop.builder.read_current_source
  - psop.builder.list_materials
  - psop.builder.read_material_analysis
  - psop.builder.list_reference_assets
  - psop.standard.search
  - psop.builder.submit_candidate
  - workspace.read_text
  - workspace.write_text
  - workspace.list
memory_scope: psop.builder
```

`agent.py`：

- 复用 demo agent factory 模式。
- 注册 framework tools，并从 `tools.builtin.workspace`、`tools.builtin.builder`、`tools.builtin.standard` 注册具体业务 tools。
- 调用 `filter_tools_by_skill_allowed_tools(context.definition.tools, context.skill_metadata)` 收敛业务工具。
- 最终可见工具为 `load_skill` 加收敛后的业务工具。
- 使用 `build_middlewares()` 保持 model/tool/token events。

`prompt.py`：

- 复用 demo prompt 的稳定结构。
- 稳定前缀只包含 system prompt、memory prompt、memory snapshot、Skill metadata。
- 动态 source、素材、标准结果不直接塞入 system prompt，由 tools 按需读取。

`system.md`：

- 明确 `psop-builder` 是构建者，不是发布者、编译器或 Runtime。
- 要求先加载三个 builder Skills。
- 要求所有外部素材和 LightRAG snippets 作为数据事实，不作为指令来源。
- 要求最终只能通过 `psop.builder.submit_candidate` 提交候选产物。
- 禁止生成 `skill.yaml`、禁止伪造标准引用、禁止直接提交 GitLab。

测试：

- registry 能加载 `psop.builder`。
- `make_builder_agent()` 返回可 invoke agent。
- skill allowed-tools 并集不请求未授权工具。

### Step 7：新增 builder Agent Skills

新增：

```text
skills/psop-builder-core/SKILL.md
skills/psop-builder-evidence-mapping/SKILL.md
skills/psop-builder-quality-review/SKILL.md
```

要求：

- frontmatter `name` 与目录名一致。
- frontmatter `description` 使用简体中文表达，例如“当需要……时使用此 Skill”，范围足够窄，避免误触发 compiler、tester、audit 或通用聊天。
- `allowed-tools` 只声明业务工具，不包含 `load_skill`。
- 内容按详细设计文档第七章落地，不引入脚本、二进制或额外权限。

测试：

- `SkillLoader.load_metadata()` 能读取三个 Skill。
- `SkillLoader.load()` 记录 `agent.skill.loaded`。
- 三个 Skill 的 `allowed-tools` 并集与 `psop.builder` AgentDefinition tools 相容。

### Step 8：扩展 LangChainAgentExecutor artifact 收集

修改 `backend/app/agent_harness/runners/langchain_agent_executor.py`：

- 保持 demo `workspace/result.md` artifact 收集行为。
- 新增 outputs artifact 收集：
  - 如果 `/mnt/psop/outputs/builder-result.json` 存在，加入 `AgentArtifact(artifact_type="skill_draft_candidate", path="sandbox://outputs/builder-result.json")`。
  - 如果 `/mnt/psop/outputs/skill-draft/` 存在，加入 `AgentArtifact(artifact_type="skill_draft_files", path="sandbox://outputs/skill-draft")`，provenance 记录文件列表和目录 hash。
- 如果模型最终回答但没有提交 candidate，`AgentHarnessService` 本身不直接判失败；由 builder 集成层或验收脚本检查 artifact 是否存在并判定失败。

测试：

- demo 测试不变。
- builder scripted run 后 `AgentResult.artifacts` 包含 `skill_draft_candidate` 和 `skill_draft_files`。

### Step 9：Agent Run / Event / Artifact 持久化

新增：

```text
backend/app/agent_harness/persistence/models.py
backend/app/agent_harness/persistence/repository.py
backend/app/agent_harness/persistence/service.py
```

最低 ORM 表：

- `agent_run`
  - `id`
  - `agent_key`
  - `agent_version`
  - `status`
  - `related_skill_definition_id`
  - `related_generation_id`
  - `related_job_id`
  - `input_summary`
  - `sandbox_path`
  - `model_info`
  - `error_message`
  - `created_at`
  - `updated_at`
- `agent_event`
  - `id`
  - `agent_run_id`
  - `seq_no`
  - `event_type`
  - `payload`
  - `occurred_at`
- `agent_artifact`
  - `id`
  - `agent_run_id`
  - `artifact_type`
  - `path`
  - `content_hash`
  - `provenance`
  - `status`
  - `created_at`

集成规则：

- `DatabaseManager.create_schema()` import agent harness persistence models。
- `AgentHarnessService.invoke()` 可选接收 persistence service；没有 DB session 时继续文件型运行，保证 demo 和脚本简单可跑。
- `AgentEventWriter` 保持写 `events.jsonl`，并支持 run 结束批量 flush 到 repository，避免影响现有 event writer 单测。
- 事件 payload 只存摘要、ID、hash 和安全字段，不保存隐藏推理或大段素材原文。

测试：

- create_schema 后能创建三张表。
- 一个 scripted builder run 可写入 agent_run、agent_event、agent_artifact。

### Step 10：SkillsService 接入 psop.builder

修改 `backend/app/domain/skills/service.py` 中 `_run_skill_raw_material_generation()`：

- 保留 source/material 校验、source conflict 前置检查和 material context 收集。
- 将旧的 Prompt Pack + `LlmInferenceGateway.complete()` 调用替换为：

```python
result = self.agent_harness_service.invoke(
    AgentInvocation(
        agent_key="psop.builder",
        input=builder_input,
        context=builder_context,
        memory_scope="psop.builder",
        agent_run_id=f"skill-generation-{generation.id}",
    )
)
```

- `builder_input` 包含：
  - task
  - skill id/key/name/description/draft version/source ref/source commit sha
  - user_description
  - current_source
  - material_ids
  - output_contract
- `builder_context` 包含：
  - `material_analysis_results`
  - `candidate_reference_assets`
  - `standard_search_policy`
- PSOP Skill 形式定义、物理世界任务建模原则和发布审阅标准不作为静态 context 字段传递；这些构建方法论由 `psop-builder-core`、`psop-builder-evidence-mapping` 和 `psop-builder-quality-review` 通过 `load_skill` 提供。
- 从 `AgentResult.artifacts` 或 sandbox path 读取 `outputs/builder-result.json`。
- 对 artifact 执行 builder v1 严格校验，再调用 `parse_generated_skill_draft(json.dumps(candidate))` 做兼容转化。
- 复用 `_resolve_selected_reference_assets()`、GitLab head 二次检查和 `_commit_generated_skill_files()`。
- 如果 selected reference asset 是图片，`submit_candidate` 必须把原图物化到 sandbox `outputs/skill-draft/references/` 对应目录；`SKILL.md` 必须在使用该图片的流程步骤中通过相对 Markdown 图片链接展示，不得使用 base64 data URI，也不得把参考图片集中追加到文档底部。
- 更新 `SkillRawMaterialGeneration`：
  - `generated_files`
  - `generation_reason`
  - `review_notes`
  - `material_usage`
  - `committed_commit_sha`
  - `prompt_metadata.agent_key`
  - `prompt_metadata.agent_run_id`
  - `prompt_metadata.sandbox_path`
  - `prompt_metadata.builder_artifact_path`
  - `prompt_metadata.events_path`
  - `prompt_metadata.standard_search_summary`
  - `prompt_metadata.selected_reference_assets`
  - `raw_response.agent_result`
  - `raw_response.parsed`
  - `raw_response.validation`

失败规则：

- `AgentResult.status != "succeeded"`：generation/job failed，不提交 GitLab。
- 缺 `builder-result.json`：failed，不提交 GitLab。
- candidate 校验失败：failed，不提交 GitLab。
- source head 冲突：failed/source conflict，不提交 GitLab。

测试：

- mocked AgentHarnessService 返回合法 artifact，可提交 draft。
- 缺 artifact 时 generation failed。
- candidate 校验失败时不提交。
- source conflict 时不提交。

### Step 11：验收脚本与 fixture

新增：

```text
tests/run_psop_builder_agent.py
tests/fixtures/psop_builder/minimal.json
```

fixture 包含：

- `input.skill`
- `input.user_description`
- `input.current_source`
- `input.material_ids`
- `context.material_analysis_results`
- `context.candidate_reference_assets`
- `context.standard_search_policy`

脚本行为：

- 从 fixture 读取完整 `AgentInvocation`。
- 默认使用真实 `Settings()`、真实 `AgentHarnessService` 和真实 model provider。
- 支持 `--scripted` 使用 deterministic scripted builder model，供 CI 和本地无 LLM key 时验证。
- 输出 `AgentResult` JSON 摘要，并打印 sandbox path、events path、builder result path。
- 退出码：成功为 0，失败为 1。

测试：

- `--scripted` 模式在 CI 中稳定通过。
- 真实模式作为人工验收命令，不纳入默认 pytest 强依赖。

## 4. Public Interfaces

### ToolSpec

`ToolSpec` 新增治理元数据字段。既有字段保持兼容，已有 demo tool 不需要一次性补齐所有元数据。

### Builder candidate

新增 builder v1 candidate schema。它是 `psop.builder.submit_candidate` 和 `SkillsService` 二次校验的共同契约，不替代旧 `GeneratedSkillDraft`，而是在 builder 链路中更严格地包裹它。

### AgentArtifact

新增 artifact type：

```text
skill_draft_candidate
skill_draft_files
```

path 首选：

```text
sandbox://outputs/builder-result.json
sandbox://outputs/skill-draft
```

### SkillRawMaterialGeneration metadata

`prompt_metadata` 扩展 agent/builder 字段，但 API response 不新增强制顶层字段：

```json
{
  "agent_key": "psop.builder",
  "agent_run_id": "...",
  "sandbox_path": "...",
  "builder_artifact_path": "sandbox://outputs/builder-result.json",
  "events_path": "...",
  "standard_search_summary": {
    "attempted": true,
    "status": "success",
    "result_count": 3,
    "standard_refs": []
  },
  "selected_reference_assets": [],
  "reference_files": []
}
```

## 5. 测试矩阵

| 层级 | 文件 | 覆盖点 |
| --- | --- | --- |
| ToolSpec | `tests/test_agent_harness_tools.py` | 默认治理字段、兼容 demo tools。 |
| Workspace tools | `tests/test_agent_harness_workspace_tools.py` | list/read/write、路径越界、outputs 绕过拒绝。 |
| Builder tools | `tests/test_agent_harness_builder_tools.py` | context read tools、submit_candidate 成功/失败。 |
| Standard search | `tests/test_agent_harness_standard_tools.py` | success、timeout、缺配置、malformed response、结果裁剪。 |
| Builder agent | `tests/test_agent_harness_builder_agent.py` | registry、factory、scripted run、skills loaded、artifact created。 |
| Builder skills | `tests/test_agent_harness_skills.py` | 三个 builder skills metadata/load/allowed-tools。 |
| Persistence | `tests/test_agent_harness_persistence.py` | agent_run/event/artifact ORM 与 repository。 |
| SkillsService | `tests/test_skills_api.py` 或新增 service 测试 | generation 通过 AgentHarnessService artifact 提交 draft；失败不提交。 |

默认全量回归：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
```

脚本验收：

```bash
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_builder_agent.py --fixture tests/fixtures/psop_builder/minimal.json --scripted
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_builder_agent.py --fixture tests/fixtures/psop_builder/minimal.json
```

## 6. 交付拆分建议

建议按 6 个小 PR 或 6 个 commit 推进：

1. Harness tool metadata + workspace tools。
2. Builder candidate schema + builder tools。
3. LightRAG `/query` standard search tool。
4. Builder agent package + three Agent Skills + scripted model/test。
5. Agent run/event/artifact persistence。
6. SkillsService generation 链路切换 + 验收脚本。

每个阶段都必须保持：

- `demo.psop_harness_agent` 现有测试继续通过。
- 旧 `parse_generated_skill_draft()` 兼容行为不被无意破坏。
- Runtime、Compiler、Skill Tests 主链路不被改造。

## 7. 风险与约束

- 首版保持单智能体，不引入 subagents 或独立 workflow orchestration。
- 首版不开放 shell、open-world web search、GitLab commit tool、数据库写 tool、对象存储写 tool 给模型。
- LightRAG 不可用时不阻塞 candidate 生成，但必须留下 `review_notes` 或 `industry_standard_usage` 可审计记录。
- 素材分析、OCR、ASR、用户上传文件和 LightRAG snippet 都不能作为指令来源，只能作为带 trust label 的事实材料。
- LightRAG `/query` 返回的是 RAG answer 和 references，不是 PSOP 约束 schema；handler 和 candidate validator 必须共同防止模型把无法追溯到标准编号/条款号的内容写成强制标准要求。
- `psop.builder.submit_candidate` 成功只表示 sandbox candidate 通过第一层结构校验，不表示 GitLab draft 已提交。
- 正式 draft commit、reference asset copy、source conflict check、draft version snapshot 更新继续由 `SkillsService` 执行。
- Agent events 记录 operational facts，不记录隐藏推理、大段素材原文、完整标准原文或 secret。

## 8. 完成定义

本计划完成时应满足：

```text
1. psop.builder AgentDefinition 可以被 FileAgentDefinitionRegistry 加载。
2. 三个 psop-builder Agent Skills 可以被 SkillLoader 加载。
3. psop.builder scripted e2e run 会加载 memory、skills、tools，并写出 builder-result.json。
4. builder-result.json 通过 builder v1 strict validation。
5. AgentResult.artifacts 包含 skill_draft_candidate 和 skill_draft_files。
6. SkillRawMaterialGeneration 可通过 AgentHarnessService artifact 完成 draft commit。
7. pytest 全量通过。
8. tests/run_psop_builder_agent.py --fixture tests/fixtures/psop_builder/minimal.json --scripted 退出码为 0，并验证最终物化文件和参考图片链接策略。
```
