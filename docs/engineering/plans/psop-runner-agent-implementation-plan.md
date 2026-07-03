# PSOP Runner Agent 实施计划

本文是阶段性实施计划，不是长期架构事实源。`psop.runner` 的职责、状态边界、输入输出契约、参考图片输出、工具、Agent Skills、上下文、安全与可观测性约束以 [PSOP Runner Agent 详细设计](../../architecture/psop-runner-agent-design.md) 为准；Runtime、Agent Harness 与 PSOP-EG 的总体边界以 [系统架构设计](../../architecture/system-architecture.md) 和 [Execution Graph formal-v5](../../architecture/execution-graph-formal-v5.md) 为准；终端展示边界以 [终端接入说明](../../guides/terminal-integration-v1.md) 为准。

> 状态：已完成（2026-07-03）。
>
> 验证：已通过 `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q tests/test_agent_harness_runner_agent.py tests/test_runtime_runner_agent_integration.py`、`PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_runner_agent.py --fixture tests/fixtures/psop_runner/minimal.json --scripted` 和 `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q`。
>
> 目标：在不替换 `RuntimeService.process_run()` 的前提下，实现 `psop.runner` Agent Harness 智能体，并让 Runtime 仅在显式 `agent_binding.agent_key="psop.runner"` 的 LLM / evidence evaluation 节点上委托给该智能体。
>
> 核心边界：`psop.runner` 只产出 `RunnerObservation`；`RuntimeService` 继续拥有 guard、selection、merge、wait checkpoint、snapshot、trace、terminal output 和 run status 的状态主权。

## 1. 目标与验收标准

实施完成后，运行期链路应支持：

```text
RuntimeService.process_run()
  -> sync terminal events into Session Token
  -> select enabled node
  -> if node.agent_binding.agent_key == "psop.runner":
       build psop.runner AgentInvocation
       AgentHarnessService.invoke(agent_key="psop.runner")
       read sandbox://outputs/runner-observation.json
       validate and map RunnerObservation
     else:
       keep existing LlmInferenceGateway path
  -> RuntimeService merge observation
  -> RuntimeService append snapshot / trace / terminal event
  -> wait / continue / halt
```

最终验收命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_runner_agent.py --fixture tests/fixtures/psop_runner/minimal.json --scripted
```

脚本成功条件：

- `AgentResult.status == "succeeded"`。
- events 中包含 `agent.memory.read`。
- events 中包含三个 `agent.skill.loaded`：`psop-runner-core`、`psop-runner-terminal-guidance`、`psop-runner-evidence-evaluation`。
- events 中包含关键 tool calls：`psop.runner.read_prompt_view`、`psop.runner.read_runtime_contract`、`psop.runner.read_current_checkpoint`、`psop.runner.list_step_reference_images`、`psop.runner.list_terminal_events`、`psop.runner.read_latest_evidence`、`psop.runner.submit_observation`。
- sandbox 中存在 `/mnt/psop/outputs/runner-observation.json`。
- `runner-observation.json` 通过 `RunnerObservation` 严格校验。
- 当 observation 包含 `reference_images` 时，Runtime 追加 `terminal.multimodal.output.v1`，并包含 text part 与 image part。
- `need_more_evidence` / `retry` 能进入 `waiting_input` 且保留 checkpoint。
- `complete` / `abort` 只能通过 Runtime halt 和状态转换落地，不能由 Agent 直接写 run status。
- 没有 `agent_binding` 的旧 compiled artifact 继续走现有 `LlmInferenceGateway` 路径。

## 2. 当前基线与差异

当前仓库已具备实现基础：

- `backend/app/agent_harness/` 已有 AgentDefinition registry、AgentHarnessService、sandbox、memory、skills、workspace tools、LangChain runner、middleware 和 AgentRun 持久化。
- `psop.builder` 和 `psop.compiler` 已按 `backend/app/agent_harness/agents/psop/<agent>/` 模式实现。
- `LangChainAgentExecutor` 已支持 builder/compiler required artifact contract，但尚未支持 runner observation artifact。
- `RuntimeService._execute_node()` 当前对 `kind == "llm"` 或 `actor == "agent.llm"` 直接调用 `LlmInferenceGateway.complete()` / `complete_multimodal()`。
- `RuntimeService._apply_node_interaction()` 当前主要追加文本 output；参考图片需要通过 output terminal parts 落地。
- `TerminalEventPart` 已支持 `direction=output` 的事件 part 存储模型；终端接入文档已经定义 `terminal.multimodal.output.v1` 的展示方式。
- `formal_v5.py` 当前不禁止 node extra fields，但 compiler allowed runtime snapshot、scaffold tool 和 compiler Skill 还没有把 `agent_binding`、`reference_images` 作为正式扩展输出。

需要实施的主要差异：

- 新增 `psop.runner` Agent 包和 Runner Agent Skills。
- 新增 Runner tool registry 与 `RunnerObservation` schema。
- 扩展 executor artifact 收集和 required artifact contract。
- 扩展 RuntimeService，使其能构造 runner AgentInvocation、调用 AgentHarnessService、读取 sandbox artifact、校验并映射 observation。
- 扩展 Runtime 输出逻辑，使 `reference_images` 转换为 `terminal.multimodal.output.v1` 的 image parts。
- 扩展 compiler scaffold / allowed runtime，使未来新编译产物能显式携带 `agent_binding` 和当前步骤参考图片。

额外实施约定：

- 智能体、tools、skills 中凡是说明性文本都使用简体中文；协议字段名、枚举值、API 路径、Python 标识符保留英文。
- Runner tools 不直接访问数据库、GitLab、对象存储或开放网络；需要的运行时事实由 `RuntimeService` 预先放入 `AgentInvocation.context`。
- 任何失败都必须成为结构化 tool result、AgentResult 或 Runtime observation，不允许让模型 tool call 悬空。

## 3. 实施阶段

### Step 1：定义 RunnerObservation schema

新增：

```text
backend/app/agent_harness/agents/psop/runner/
  schemas.py
```

建议常量：

```python
RUNNER_OBSERVATION_VIRTUAL_PATH = "/mnt/psop/outputs/runner-observation.json"
RUNNER_OBSERVATION_SCHEMA = "psop.runner.observation.v1"
RUNNER_DECISIONS = {"continue", "need_more_evidence", "retry", "abort", "complete"}
SUPPORTED_TERMINAL_INPUT_KINDS = {"text", "image", "audio", "video"}
```

实现要求：

- 提供 `validate_runner_observation(payload, *, node_id, output_contract, step_reference_images, terminal_cursor) -> RunnerObservation` 或等价函数。
- 顶层必须是 object，`schema` 必须为 `psop.runner.observation.v1`。
- `node_id` 必须等于当前 Runtime 节点 ID。
- `decision` 必须属于 output contract 允许集合。
- `terminal_message` 必须为简体中文终端展示文本，不得包含对象存储 key、隐藏推理、数据库内部 ID 或 credential。
- `next_phase` 只能为空、当前节点允许的 next phase、wait.resume_phase 或 runtime contract 中合法 phase。
- `expected_inputs` 只能包含 `text`、`image`、`audio`、`video`。
- `reference_images` 只能引用当前 `step_reference_images` 中存在的 `reference_image_ref`，并按 `display_order` 稳定排序。
- `source_refs` 中的 terminal event 引用不得晚于当前 terminal cursor。
- `final_response` 只允许在 `decision in {"complete", "abort"}` 时非空。

测试：

- 最小合法 observation 通过。
- 错误 schema、错误 node_id、非法 decision、非法 expected input、跨步骤 reference image、未来 terminal_event ref、越权 final_response 均失败。

### Step 2：实现 Runner tools

新增：

```text
backend/app/agent_harness/tools/builtin/runner.py
```

注册：

- `psop.runner.read_prompt_view`
- `psop.runner.read_runtime_contract`
- `psop.runner.read_current_checkpoint`
- `psop.runner.list_step_reference_images`
- `psop.runner.list_terminal_events`
- `psop.runner.read_terminal_event_part`
- `psop.runner.read_latest_evidence`
- `psop.runner.submit_observation`

通用规则：

- 所有工具使用严格 JSON Schema，`additionalProperties=false`。
- 所有 read tools 只读取 `ToolExecutionContext.invocation_context` 和 `invocation_input`。
- 大字段按 `max_result_chars` 裁剪，返回 `truncated=true`。
- 错误统一返回 `status=error`、`type`、`message`、`retryable`、`next_valid_actions`。
- `submit_observation` 调用 Step 1 schema 校验后写入 `/mnt/psop/outputs/runner-observation.json`，并记录 `agent.runner.observation.submitted` 和 `agent.artifact.created`。

工具行为：

- `read_prompt_view` 返回当前节点 Prompt View 和 trust label。
- `read_runtime_contract` 返回 runtime_contract 摘要、workflow step、evidence requirements、安全约束和合法 phases。
- `read_current_checkpoint` 返回 checkpoint、expected inputs、resume phase、wait evidence。
- `list_step_reference_images` 返回当前步骤可选参考图片，包含 `reference_image_ref`、`title`、`caption`、`workflow_step_id`、`artifact_ref`、`source_ref`。
- `list_terminal_events` 按 seq 范围返回事件摘要和 part 摘要，不返回原始二进制。
- `read_terminal_event_part` 只返回已授权 part 的安全摘要或内容引用；图片/音视频默认返回 metadata 和 artifact ref，不直接把大二进制塞入模型上下文。
- `read_latest_evidence` 返回最新 evidence bundle。

测试：

- read tools 在缺少 context 时返回结构化 error。
- `list_step_reference_images` 只返回当前步骤图片。
- `submit_observation` 成功写 sandbox artifact；失败不写 artifact。
- 所有工具事件可在 AgentEvent 中审计。

### Step 3：新增 Runner Agent 包

新增：

```text
backend/app/agent_harness/agents/psop/runner/
  __init__.py
  agent.py
  prompt.py
  agent.yaml
  system.md
  schemas.py
```

`agent.yaml` 对齐架构设计：

```yaml
agent_key: psop.runner
version: v1
runner_kind: langchain_agent
factory: make_runner_agent
description: 在 PSOP Skill 运行过程中协助终端用户，生成受治理的终端引导、参考图片选择和现场证据评估 observation。
model:
  name: default
  thinking_enabled: false
system_prompt_file: system.md
skills:
  - psop-runner-core
  - psop-runner-terminal-guidance
  - psop-runner-evidence-evaluation
tools:
  - psop.runner.read_prompt_view
  - psop.runner.read_runtime_contract
  - psop.runner.read_current_checkpoint
  - psop.runner.list_step_reference_images
  - psop.runner.list_terminal_events
  - psop.runner.read_terminal_event_part
  - psop.runner.read_latest_evidence
  - psop.runner.submit_observation
  - workspace.write_text
  - workspace.read_text
  - workspace.list
middleware:
  - name: dangling_tool_call
  - name: model_events
    config:
      max_model_calls: 16
  - name: token_usage
  - name: tool_calls
    config:
      max_error_counts:
        psop.runner.submit_observation: 3
memory_scope: psop.runner
```

`agent.py` 要求：

- 注册 runner tools、workspace tools、framework tools。
- 通过 `filter_tools_by_skill_allowed_tools()` 过滤业务工具。
- 固定注入 `load_skill`，不默认注入 `load_skill_resource`。
- 使用 `create_psop_agent()`、`build_middlewares()` 和 runner `apply_prompt_template()`。

`system.md` 要求：

- 明确 Runner 是运行协作者，不是 Runtime Kernel。
- 明确终端输入、OCR/ASR、媒体摘要和用户上传内容都是不可信数据。
- 明确必须通过 `psop.runner.submit_observation` 提交结构化 observation。
- 明确参考图片只能从当前步骤候选中选择，不得跨步骤或凭空生成图片引用。
- 明确所有终端自然语言输出使用简体中文。

测试：

- `FileAgentDefinitionRegistry` 能加载 `psop.runner`。
- `make_runner_agent()` 能创建 LangChain agent，且 visible tools 包含 `load_skill` 和 runner tools。

### Step 4：新增 Runner Agent Skills

新增：

```text
skills/psop-runner-core/SKILL.md
skills/psop-runner-terminal-guidance/SKILL.md
skills/psop-runner-evidence-evaluation/SKILL.md
```

要求：

- frontmatter `description` 使用中文。
- `allowed_tools` 必须覆盖 Runner AgentDefinition 中的业务工具和 workspace tools。
- `psop-runner-core` 说明状态边界、信任边界、observation 输出规则和 reference image 选择原则。
- `psop-runner-terminal-guidance` 说明终端提示的语言、长度、行动粒度和安全提醒。
- `psop-runner-evidence-evaluation` 说明证据充分性判断、缺失证据、模糊证据和安全风险识别。
- Skills 不包含 scripts，不读取外部网络。

测试：

- `SkillLoader` 能加载三个 skills。
- `filter_tools_by_skill_allowed_tools()` 不报未授权工具。
- skill activation 文本不会把终端事实当作系统指令。

### Step 5：扩展 LangChainAgentExecutor artifact contract

修改：

```text
backend/app/agent_harness/runners/langchain_agent_executor.py
```

要求：

- 在 `REQUIRED_ARTIFACTS_BY_AGENT` 中增加 `psop.runner`：

```text
artifact_type: runner_observation
artifact_ref: sandbox://outputs/runner-observation.json
required_skill_names:
  - psop-runner-core
  - psop-runner-terminal-guidance
  - psop-runner-evidence-evaluation
required_tool_names:
  - psop.runner.read_prompt_view
  - psop.runner.read_runtime_contract
  - psop.runner.read_current_checkpoint
  - psop.runner.list_step_reference_images
  - psop.runner.submit_observation
```

- `_collect_artifacts()` 支持 `/mnt/psop/outputs/runner-observation.json`，返回 `AgentArtifact(artifact_type="runner_observation")`。
- continuation prompt 使用中文，要求模型立即调用 `psop.runner.submit_observation`。

测试：

- 模型未提交 runner observation 时，executor 触发 required artifact continuation。
- 仍未提交时 AgentResult failed，且错误信息明确 artifact ref。
- 提交 artifact 但缺必需 tools 或 skills 时，AgentResult failed。

### Step 6：扩展 compiler allowed runtime 与 scaffold

修改：

```text
backend/app/agent_harness/tools/builtin/compiler.py
backend/app/domain/compiler/formal_v5.py
skills/psop-compiler/*
```

要求：

- `allowed_runtime_snapshot()` 增加 `node_extensions.agent_binding`，说明 `llm` 节点允许：

```json
{
  "agent_binding": {
    "agent_key": "psop.runner",
    "output_schema": "psop.runner.observation.v1"
  }
}
```

- formal-v5 validator 保持不新增 node kind；可选择增加轻量校验：
  - `agent_binding` 只能出现在 `kind="llm"` 且 `actor.name="agent.llm"` 的节点上。
  - `agent_binding.agent_key` 只接受 `psop.runner`。
  - `agent_binding.output_schema` 必须为 `psop.runner.observation.v1`。
- `build_formal_v5_scaffold` 在 `instruct_<step_id>` 和 `evaluate_<step_id>` 节点上写入 `agent_binding`。
- `runtime_contract.workflow_steps[].reference_images` 从 builder selected reference assets / source references 透传到 runtime contract。
- `allowed_runtime_snapshot().formal_v5_contract` 补充 reference image 输出规则。
- `psop-compiler` Skill resources 更新：编译运行期 LLM 节点时应优先使用 `psop.runner` agent_binding。

测试：

- scaffold 生成的 LLM 节点包含 `agent_binding`。
- 含合法 `agent_binding` 的 artifact 通过 validator。
- 非 LLM 节点上的 `agent_binding` 被拒绝。
- 非 `psop.runner` agent key 被拒绝。
- workflow step reference images 能进入 runtime_contract。

### Step 7：RuntimeService 接入 AgentHarnessService

修改：

```text
backend/app/domain/runtime/service.py
backend/app/api/dependencies.py
backend/app/app.py 或服务装配位置
```

要求：

- `RuntimeService` 构造函数增加可选 `agent_harness_service: AgentHarnessService | None`，保持旧调用兼容。
- `_execute_node()` 在 LLM 分支前检查 `node.agent_binding.agent_key == "psop.runner"`。
- 无 `agent_binding` 或 `agent_harness_service is None` 时继续旧 `LlmInferenceGateway` 路径。
- 新增内部函数：
  - `_execute_runner_agent_node(session, node, token, artifact_payload) -> dict[str, Any]`
  - `_build_runner_agent_invocation(...) -> AgentInvocation`
  - `_load_runner_observation(result: AgentResult) -> dict[str, Any]`
  - `_validate_and_map_runner_observation(...) -> dict[str, Any]`
- `AgentInvocation.context` 必须只包含 Prompt View、runtime contract slice、current checkpoint、terminal event summaries、latest evidence、step reference images、trace summary 和 allowed runtime，不包含完整数据库对象。
- Agent run 持久化上下文应关联 `related_skill_definition_id`、`related_job_id` 或 `run_id`，便于 replay 追踪。
- Runtime trace payload 包含 `agent_key`、`agent_run_id`、`runner_observation_ref`、`decision`、`source_refs`、`reference_images`。

测试：

- 旧 artifact 无 `agent_binding` 时仍调用 `LlmInferenceGateway`。
- 新 artifact 有 `agent_binding` 时调用 `AgentHarnessService.invoke(agent_key="psop.runner")`。
- AgentResult failed 时 Runtime 进入可恢复失败路径或结构化 failure，不丢 wait checkpoint。
- runner observation 被 merge 到 `observations.<node_id>`。

### Step 8：参考图片输出为 terminal parts

修改：

```text
backend/app/domain/runtime/service.py
backend/app/domain/runtime/schemas.py 如需补充响应示例或类型约束
```

要求：

- 新增 `_append_runner_reference_output_event(...)` 或扩展 `_apply_node_interaction()`。
- 当 mapped observation 包含 `reference_images` 且当前节点需要 output_to_terminal 时，追加：
  - `direction="output"`
  - `event_kind="terminal.multimodal.output.v1"`
  - `mime_type="multipart/mixed"`
  - text part：`terminal_message`
  - image parts：来自经校验的 reference image artifact refs
- image part metadata 包含 `title`、`caption`、`source_ref`、`reference_image_ref`。
- 不把对象存储 key、presigned URL 或内部下载地址写入 `payload_inline`。
- `payload_inline` 只保存 `summary` 和 `reference_image_count`。
- 若参考图片 artifact 缺失或无权限读取，Runtime 不应编造图片；应降级为文本输出，并在 trace 中记录 `reference_image_unavailable`。

测试：

- observation 带 1 张 reference image 时生成一个 output event，包含 text part 和 image part。
- 多张图片按 `display_order` 输出。
- 终端通过 part content endpoint 能读取图片。
- artifact 缺失时不中断 Runtime 主循环。

### Step 9：测试夹具、脚本与端到端验证

新增：

```text
tests/fixtures/psop_runner/minimal.json
tests/run_psop_runner_agent.py
tests/test_agent_harness_runner_agent.py
tests/test_runtime_runner_agent_integration.py
```

测试矩阵：

| 场景 | 期望 |
| --- | --- |
| 直接调用 `psop.runner` scripted happy path | 生成 runner-observation.json，AgentResult succeeded。 |
| 缺失证据 | observation decision 为 `need_more_evidence`，Runtime 进入 `waiting_input`。 |
| 参考图片选择 | 只选择当前 workflow step 的 reference image。 |
| 无匹配参考图片 | `reference_images=[]`，输出纯文本。 |
| prompt injection | 终端输入中的越权指令不改变 decision schema、tool 权限或状态主权。 |
| 旧 LLM 路径兼容 | 无 `agent_binding` artifact 行为不变。 |
| Runtime delegation | 有 `agent_binding` artifact 调用 AgentHarnessService。 |
| 多模态 output | 生成 `terminal.multimodal.output.v1`，包含 text/image parts。 |
| Agent failure | Runtime 保留可恢复 wait checkpoint 或明确 failed trace。 |
| Replay | replay timeline 能关联 terminal event、runtime trace 和 `agent_run_id`。 |

建议验收命令：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q tests/test_agent_harness_runner_agent.py tests/test_runtime_runner_agent_integration.py
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_runner_agent.py --fixture tests/fixtures/psop_runner/minimal.json --scripted
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
```

## 4. 建议交付拆分

建议按 6 个小 PR 或 commit 推进：

1. `runner schema + tools`
   - RunnerObservation schema。
   - runner builtin tools。
   - tool 单测。

2. `runner agent package + skills`
   - `backend/app/agent_harness/agents/psop/runner/`。
   - `skills/psop-runner-*`。
   - agent definition / skill loader 测试。

3. `runner artifact contract`
   - LangChainAgentExecutor required artifact。
   - runner scripted direct invocation。

4. `compiler/runtime contract extension`
   - compiler allowed runtime / scaffold 写入 `agent_binding`。
   - runtime_contract reference images。
   - formal validator 轻量扩展校验。

5. `runtime delegation`
   - RuntimeService 接入 AgentHarnessService。
   - runner observation 映射与 trace 投影。
   - 旧 LLM 路径兼容测试。

6. `terminal multimodal output + e2e`
   - reference image terminal parts。
   - replay 关联。
   - 端到端脚本与完整测试。

## 5. 完成定义

本计划完成需要同时满足：

- `psop.runner` AgentDefinition、system prompt、Agent Skills、tools 和 schema 已实现。
- `AgentHarnessService.invoke(agent_key="psop.runner")` 可通过 scripted fixture 生成合法 `runner-observation.json`。
- RuntimeService 只在显式 `agent_binding` 节点委托给 `psop.runner`。
- 旧 Runtime LLM 路径保持兼容。
- `RunnerObservation` 的 `decision`、`expected_inputs`、`reference_images`、`source_refs`、`final_response` 均由确定性代码校验。
- 参考图片能通过 `terminal.multimodal.output.v1` 输出给终端，终端仍通过 part content endpoint 读取图片。
- Runtime trace 和 replay 能关联 `agent_run_id`、runner observation artifact 和 terminal output event。
- 所有新增测试和现有后端测试通过。
