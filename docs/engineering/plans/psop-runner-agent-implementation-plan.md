# PSOP Runner Agent 实施计划

本文是阶段性实施计划，不是长期架构事实源。`psop.runner` 的职责、工具、Agent Skills、RuntimeService 接入方式、终端协作、证据评估、参考图片输出和 observation 契约以 [PSOP Runner Agent 详细设计](../../architecture/psop-runner-agent-design.md) 为准；Agent Harness 总体边界以 [系统架构设计](../../architecture/system-architecture.md) 为准；PSOP-EG 与 Session Token 的形式语义以 [Execution Graph formal-v5](../../architecture/execution-graph-formal-v5.md) 为准；终端事件与多模态输出边界以 [终端接入说明](../../guides/terminal-integration-v1.md) 为准。

> 状态：已实现；`psop.runner` 已成为 Runtime LLM / evidence evaluation 节点的默认执行路径；AgentRun Runtime 关联、参考图片 warning trace、严格 source ref 校验和终端上传图片的 Harness multimodal attachment 直连识别已补齐。Runtime 不生成图片 safe summary，图片语义判断由 `psop.runner` 多模态模型完成。
>
> 最近验证：`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q tests/test_agent_harness_runner.py tests/test_agent_harness_persistence.py tests/test_runtime_services.py tests/test_skills_api.py`、`PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_runner_agent.py --fixture tests/fixtures/psop_runner/minimal.json --scripted`、`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q`、`git diff --check` 均已通过。
>
> 制定日期：2026-07-03。
>
> 核心目标：在不改变 `RuntimeService` 状态主权和既有 Runtime 主循环的前提下，实现 `psop.runner` Agent Harness 首版接入，用它替代 Runtime LLM / evidence evaluation 节点中直接调用 `LlmInferenceGateway` 的协作能力。

## 1. 目标与验收标准

首版目标不是把 `RuntimeService` 改造成 Agent，也不是引入多智能体编排，而是把当前 Runtime LLM 节点的模型调用替换为受 Agent Harness 治理的 `psop.runner` 调用：

```text
RuntimeService.process_run()
  -> sync terminal events into Session Token
  -> select enabled LLM / evidence evaluation node
  -> build psop.runner AgentInvocation
  -> AgentHarnessService.invoke(agent_key="psop.runner")
  -> runner loads Agent Skills
  -> runner reads runtime context through narrow tools
  -> runner calls psop.runner.submit_observation
  -> RuntimeService validates runner-observation.json
  -> map to existing runtime observation
  -> _merge_observation()
  -> _apply_node_interaction()
  -> _append_runtime_step()
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
- events 中包含 `agent.skill.loaded`：`psop-runner`。
- events 中包含三个 `agent.skill.resource.loaded`：`core/SKILL.md`、`terminal-guidance/SKILL.md`、`evidence-evaluation/SKILL.md`。
- events 中包含关键 tool calls：`psop.runner.read_prompt_view`、`psop.runner.read_runtime_contract`、`psop.runner.read_current_checkpoint`、`psop.runner.list_terminal_events`、`psop.runner.read_latest_evidence`、`psop.runner.list_step_reference_images`、`psop.runner.submit_observation`。
- sandbox 中存在 `/mnt/psop/outputs/runner-observation.json`。
- `runner-observation.json` 通过 `psop.runner.observation.v1` 严格校验。
- Runtime 集成测试中，Runtime LLM / evaluation 节点默认走 `psop.runner`，并产生 `runtime.agent.completed` trace。
- Runtime runner 调用持久化 `agent_run`、`agent_event` 和 `agent_artifact`，`agent_run.related_runtime_run_id` 指向当前 Run。
- 无法解析参考图片 artifact 时退化为文本输出并记录 `runtime.runner.reference_image.warning` trace。
- Runtime 落地仍只通过 `_merge_observation()`、`_apply_node_interaction()` 和 `_append_runtime_step()`。
- 既有终端输入、wait checkpoint、semantic abort、success halt、replay 和 recoverable terminal turn failure 行为不退化。

## 2. 当前基线与差异

当前代码已经具备实现 `psop.runner` 的大部分底座：

- `backend/app/agent_harness/` 已有 service、registry、LangChain runner、sandbox、memory、events、tool registry、middleware、persistence 和 scripted model 测试模式。
- `psop.builder` 与 `psop.compiler` 已按 `backend/app/agent_harness/agents/psop/<agent>/` 包结构实现，可复用 agent package、tool 注册、Skill 加载和 required artifact contract 模式。
- `ToolSpec` 已有 `risk_class`、`side_effect_class`、`resource_scope`、`permission_policy`、`timeout_seconds`、`max_result_chars` 等治理字段。
- `workspace.read_text`、`workspace.write_text`、`workspace.list` 已可复用。
- `RuntimeService` 原先在 `_execute_node()` 的 LLM 分支直接调用 `LlmInferenceGateway.complete()` 或 `complete_multimodal()`；当前已替换为 `psop.runner` observation 路径。
- `RuntimeService` 已有正式落地路径：`_merge_observation()`、`_apply_node_interaction()`、`_append_runtime_step()`、terminal event append、wait checkpoint、snapshot、trace 和 replay。

实施中已处理或保留的差异：

- `RuntimeService.__init__()` 要求接收 `AgentHarnessService`；`get_runtime_service()`、`RuntimeJobWorker` 和 `SkillTestService` 默认传入 Agent Harness，不再保留 Runtime LLM 执行开关。
- `AgentRunRecord` 已补齐 `related_runtime_run_id`，Runtime trace 也保存 `agent_run_id`，Replay 可从 Runtime trace 定位 Agent timeline。
- 现有 Runtime evaluation 解析接受 `proceed`、`retry`、`need_more_evidence`、`abort`、`complete`；Runner 详细设计使用 `continue`、`need_more_evidence`、`retry`、`abort`、`complete`。首版必须做确定性兼容，建议在 Runner observation 映射层把 `continue` 规范化为 Runtime 内部的 `proceed`，同时保留原始 runner decision。
- Harness model factory / executor 已扩展受控 multimodal attachment 路径；`psop.runner` 在存在终端上传图片附件时使用多模态模型直接识别图片，Runtime 不再生成图片 safe summary。
- 参考图片输出已接入 `terminal.multimodal.output.v1`；无法解析 artifact 时退化为文本输出并记录 warning trace。
- `backend/README.md` 仍描述为 scaffold-only，和当前 `backend/app/domain/*`、`agent_harness/*` 代码事实不一致。本计划按当前代码、架构文档和测试事实制定，不以该 README 的过期描述作为实现边界。

额外实施约定：

- `psop.runner` 是 Runtime 内部 LLM / evidence evaluation 节点的受治理协作者，不是外层 `psop_runtime` 的状态主权者。
- 首版保持单智能体，不引入 subagents、workflow orchestration、开放网络、shell、浏览器或任意 MCP connector。
- Runner 工具只读取 `AgentInvocation.context`、`AgentInvocation.input` 和 sandbox；不能直接读取数据库、对象存储 secret、MinIO object key、GitLab token 或终端鉴权 token。
- 所有面向终端用户的自然语言使用简体中文；协议字段名、枚举值、API 路径、Python 标识符保留英文。

## 3. 实施阶段

### Step 1：定义 RunnerObservation schema 与校验

新增：

```text
backend/app/agent_harness/agents/psop/runner/
  __init__.py
  schemas.py
```

实现内容：

- 定义常量：
  - `RUNNER_OBSERVATION_SCHEMA = "psop.runner.observation.v1"`
  - `RUNNER_OBSERVATION_VIRTUAL_PATH = "/mnt/psop/outputs/runner-observation.json"`
  - `RUNTIME_DECISION_BY_RUNNER_DECISION = {"continue": "proceed", ...}`
- 定义 `RunnerObservation` Pydantic schema，字段覆盖设计文档：
  - `schema`
  - `node_id`
  - `decision`
  - `terminal_message`
  - `reason`
  - `next_phase`
  - `wait_reason`
  - `expected_inputs`
  - `evidence_assessment`
  - `reference_images`
  - `safety_flags`
  - `final_response`
  - `source_refs`
  - `confidence`
- 提供 `validate_runner_observation(candidate, context) -> dict[str, Any]`：
  - `schema` 固定为 `psop.runner.observation.v1`。
  - `node_id` 必须等于当前 `AgentInvocation.input.node.id`。
  - `decision` 必须属于 output contract 允许集合。
  - `expected_inputs` 只能包含 `text`、`image`、`audio`、`video`。
  - `terminal_message` 不超过 `allowed_runtime.max_terminal_message_chars`，默认 2000。
  - `reference_images` 只能引用 invocation context 中 `step_reference_images` 的 `reference_image_ref`。
  - `source_refs` 中的 `terminal_event:<seq>` 必须存在且不晚于当前 terminal cursor。
  - `final_response` 只允许在 `decision=complete` 或 `decision=abort` 时非空。

测试：

- 最小合法 observation 通过。
- `node_id` 错误、非法 decision、非法 expected input、超长 terminal message、伪造 terminal_event ref、伪造 reference image ref 均失败。
- `decision=continue` 可输出 runner 原始 decision，同时映射出 Runtime 内部 `proceed`。

### Step 2：实现 runner narrow tools

新增：

```text
backend/app/agent_harness/tools/builtin/runner.py
```

注册工具：

- `psop.runner.read_prompt_view`
- `psop.runner.read_runtime_contract`
- `psop.runner.read_current_checkpoint`
- `psop.runner.list_step_reference_images`
- `psop.runner.list_terminal_events`
- `psop.runner.read_terminal_event_part`
- `psop.runner.read_latest_evidence`
- `psop.runner.submit_observation`

通用规则：

- 所有读取工具只从 `ToolExecutionContext.invocation_context` 和 `invocation_input` 读取。
- 工具结果统一包含 `status`、`summary`、`items` 或结构化字段、`truncated`、`next_valid_actions`。
- 错误返回结构化 result，不抛出未审计异常。
- 不返回对象存储 key、内部下载 URL、credential、完整原始媒体 bytes 或隐藏配置。
- 大字段按 `ToolSpec.max_result_chars` 裁剪并标记 `truncated=true`。

`psop.runner.submit_observation`：

- 直接接收完整 observation 参数，不接受 workspace 文件路径替代正式产物。
- 调用 Step 1 的严格校验。
- 写入 `/mnt/psop/outputs/runner-observation.json`。
- 记录 `agent.runner.observation.submitted` 和 `agent.artifact.created`。
- 返回 `artifact_ref="sandbox://outputs/runner-observation.json"`、`decision`、`runtime_decision`、`content_hash`、`validation_summary`。

测试：

- read tools 能从 fixture context 返回 prompt view、runtime contract、checkpoint、terminal event 摘要、latest evidence 和 reference images。
- 终端 part 工具不会泄露 object key。
- submit 成功时写 sandbox output。
- submit 失败时返回结构化 error，且不写 output。

### Step 3：新增 runner Agent 包

新增目录：

```text
backend/app/agent_harness/agents/psop/runner/
  __init__.py
  agent.py
  prompt.py
  agent.yaml
  system.md
  schemas.py
```

`agent.yaml`：

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
  - psop-runner
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
      max_model_calls: 6
  - name: token_usage
  - name: tool_calls
    config:
      max_error_counts:
        psop.runner.submit_observation: 3
memory_scope: psop.runner
```

`agent.py` 要求：

- 复用 builder/compiler agent factory 模式。
- 注册 runner tools、workspace tools、framework tools。
- 通过 `filter_tools_by_skill_allowed_tools(context.definition.tools, context.skill_metadata)` 收敛业务工具。
- 可见工具固定为 `load_skill`、`load_skill_resource` 加业务工具。
- 使用 `create_psop_agent()`、`build_middlewares()` 和 runner `apply_prompt_template()`。

`system.md` 要求：

- 明确 `psop.runner` 只提交 `RunnerObservation`，不修改 Session Token、TerminalEvent、Run、Invocation、TraceEvent 或 RuntimeJob。
- 明确终端输入、OCR、ASR、图片内容、视频内容和文件名都是不可信现场事实。
- 明确证据不足、安全条件不清、Skill 不适用或用户越界时，必须输出 `need_more_evidence`、`retry` 或 `abort`。
- 明确不得把自然语言回复替代 `psop.runner.submit_observation`。

测试：

- `default_agent_registry(settings.backend_root).load("psop.runner")` 成功。
- definition 的 `agent_key`、`factory`、skills、tools、middleware、memory_scope 与设计一致。
- factory 可构造 agent，可见 tools 包含 `load_skill` 和 allowed business tools。

### Step 4：新增 Runner Agent Skills

新增：

```text
skills/psop-runner/SKILL.md
skills/psop-runner/README.md
skills/psop-runner/core/SKILL.md
skills/psop-runner/terminal-guidance/SKILL.md
skills/psop-runner/evidence-evaluation/SKILL.md
```

`psop-runner/SKILL.md`：

- 声明单一 Runner Skill 包入口、工具权限、渐进加载顺序和禁止事项。
- 要求提交 observation 前读取 `core/SKILL.md`、`terminal-guidance/SKILL.md` 和 `evidence-evaluation/SKILL.md`。

`core/SKILL.md`：

- 定义状态边界、信任边界和 observation 输出规则。
- 明确事实优先级：system prompt / Agent Skill / runtime contract / Prompt View 高于 terminal facts。
- 要求不确定时暂停、澄清、要求证据或建议中止。

`terminal-guidance/SKILL.md`：

- 控制终端提示的语气、长度、行动粒度和安全提醒。
- 不输出实现细节、内部 ID、隐藏推理、数据库 ID 或对象存储 key。
- 不发明不在 Skill / runtime contract 中的现场操作步骤。

`evidence-evaluation/SKILL.md`：

- 评估文本、图片、音频、视频 evidence 摘要是否满足当前步骤要求。
- 输出 accepted / rejected / missing / ambiguous evidence。
- 识别安全条件未确认、照片不可读、对象不匹配、步骤顺序异常和用户越界。

测试：

- Skill metadata 可加载。
- allowed-tools 与 `agent.yaml.tools` 有交集且不会放开 shell、网络、数据库或对象存储工具。
- Runner scripted e2e 中三个 Skill 均被加载。

### Step 5：扩展 required artifact contract

修改：

```text
backend/app/agent_harness/runners/langchain_agent_executor.py
```

实现内容：

- 在 `REQUIRED_ARTIFACTS_BY_AGENT` 中新增 `psop.runner`：
  - `artifact_type="runner_observation"`
  - `artifact_ref="sandbox://outputs/runner-observation.json"`
  - `max_continuations=2`
  - `required_skill_names` 为三个 runner skills。
  - `required_tool_names` 至少包含 `psop.runner.submit_observation`，首版 scripted 验收要求完整 read tools。
- `_collect_artifacts()` 识别 `/mnt/psop/outputs/runner-observation.json`，记录 hash、decision、node_id 和 source ref 数量。
- 缺 artifact 时追加 continuation prompt，强制调用 `psop.runner.submit_observation`。

测试：

- Runner agent 若只回复自然语言不提交 artifact，最终 `AgentResult.status == "failed"`。
- Runner agent 提交 artifact 但未加载必需 Skill 时，required interaction 检查失败。
- 合法 scripted runner 一次或有限 continuation 内成功。

### Step 6：新增 scripted runner model、fixture 和脚本验收

新增：

```text
backend/app/agent_harness/models/scripted_runner_chat_model.py
tests/fixtures/psop_runner/minimal.json
tests/run_psop_runner_agent.py
tests/test_agent_harness_runner.py
```

`ScriptedRunnerChatModel` 行为：

1. 调用 `load_skill("psop-runner")`。
2. 调用 `load_skill_resource("psop-runner", "core/SKILL.md")`。
3. 调用 `load_skill_resource("psop-runner", "terminal-guidance/SKILL.md")`。
4. 调用 `load_skill_resource("psop-runner", "evidence-evaluation/SKILL.md")`。
5. 调用 runner read tools 读取 prompt view、runtime contract、checkpoint、terminal events、latest evidence 和 reference images。
6. 调用 `psop.runner.submit_observation` 写入合法 observation。

fixture 覆盖：

- 一个 `evaluate_collect_context` 节点。
- 一个当前 wait checkpoint。
- 至少一条 terminal input event。
- 一个 runtime contract workflow step。
- 可选一个当前步骤 reference image，用于校验引用路径。

测试：

- scripted runner e2e 满足第 1 节脚本验收条件。
- 产物可被 schema 再次读取和校验。
- events 中不会记录完整隐藏推理或对象存储 key。

### Step 7：RuntimeService 构造与调用接入

修改：

```text
backend/app/domain/runtime/service.py
backend/app/api/dependencies.py
backend/app/domain/jobs/worker.py
```

实现内容：

- `RuntimeService.__init__()` 增加必需参数 `agent_harness_service: AgentHarnessService`。
- API 依赖 `get_runtime_service()` 传入 app state 中的 `AgentHarnessService`。
- `RuntimeJobWorker` 的 runtime job 分支传入 `self.agent_harness_service`。
- 在 `_execute_node()` 的 LLM 分支中：
  - 直接调用 `_execute_runner_agent_node()`。
  - 不再通过配置开关或未注入 harness 的隐式分支回退到旧 `LlmInferenceGateway` 路径。
- `_execute_runner_agent_node()` 只返回 runtime observation，不直接修改 token、run、terminal event 或 trace。

测试：

- 默认 runtime service fixture 注入 scripted runner service，Runtime LLM 节点不调用 `FailingInferenceGateway`。
- API 依赖、worker 和 SkillTestService 构造路径均能把 service 传到 RuntimeService。

### Step 8：构造 Runner AgentInvocation 与结果映射

在 `RuntimeService` 中新增私有方法：

```text
_build_runner_invocation(...)
_build_runner_context(...)
_read_runner_observation_artifact(...)
_map_runner_observation_to_runtime_observation(...)
_validate_runner_runtime_observation(...)
```

`AgentInvocation.input` 至少包含：

- `task="assist_psop_runtime_node"`
- `run_id`
- `node.id`
- `node.kind`
- `node.actor`
- `node.mode`
- `output_contract`
- `text`

`AgentInvocation.context` 至少包含：

- `trust_labels`
- `prompt_view`
- `runtime_contract`
- `step_reference_images`
- `current_checkpoint`
- `terminal_events`
- `latest_evidence`
- `trace_summary`
- `allowed_runtime`

映射规则：

- `RunnerObservation.terminal_message` 映射为 runtime observation 的 `terminal_message` 和 `content`。
- `decision=continue` 映射为 runtime 内部 `decision=proceed`，并在 `runner.original_decision` 保存原值。
- `decision=need_more_evidence`、`retry`、`abort`、`complete` 保持原值。
- `reason`、`next_phase`、`wait_reason`、`expected_inputs`、`final_response`、`evidence_assessment`、`reference_images`、`safety_flags` 保留。
- 增加 `runner.agent_run_id`、`runner.artifact_ref`、`runner.source_refs`、`runner.reference_images`。
- trace event 类型建议为 `runtime.agent.completed`，payload 保存 agent key、agent run id、node id、decision、artifact ref、source refs、reference images 和 token usage summary。

测试：

- Runner observation 可以被映射为现有 `_merge_observation()` 可消费的 observation。
- `continue` 到 `proceed` 的兼容不改变已有 formal-v5 merge/halt 行为。
- `need_more_evidence` 和 `retry` 进入 wait。
- `abort` 进入 semantic abort。
- `complete` 走既有 success halt 路径。

### Step 9：参考图片与 `terminal.multimodal.output.v1`

首版处理原则：

- 如果 `reference_images=[]`，保持现有文本 output 行为。
- 如果 `reference_images` 非空，RuntimeService 必须再次校验这些引用来自当前 `AgentInvocation.context.step_reference_images`。
- RuntimeService 负责把 reference image 解析为 output terminal part，不让 Runner 直接追加 terminal event。
- 如果当前 runtime contract 没有可解析 `artifact_object_id` 或受控 artifact ref，不得伪造图片输出；应退化为文本提示并记录 warning trace。

实现内容：

- 新增 `_append_runner_terminal_output()` 或扩展 `_apply_node_interaction()`，在 observation 包含 runner reference images 时生成 `terminal.multimodal.output.v1`。
- text part 使用 `terminal_message`。
- image part 使用经校验的 artifact object id、mime type、title、caption、source_ref、reference_image_ref。
- `payload_inline` 使用事件级摘要：

```json
{
  "summary": "请补充清晰照片。",
  "reference_image_count": 1
}
```

测试：

- 有参考图时追加 `terminal.multimodal.output.v1`，parts 包含 text 和 image。
- 终端 part 不暴露 MinIO object key。
- 伪造 reference image ref 被拒绝。
- 无可解析 artifact 时不崩溃，保留文本输出并记录 trace warning。

### Step 10：多模态证据语义补齐

状态：已完成。当前实现路径为 Agent Harness multimodal attachment；Runtime safe summary 仅保留为未来降级选项，不作为默认方案。

已实现：

- RuntimeService 通过 `artifact_object_id` 鉴权读取终端上传的 `image/*` part，并作为 Agent Harness 多模态 attachment 传给 `psop.runner`。
- `psop.runner` 使用多模态 LLM 直接识别本次 invocation 的图片附件，并通过 `submit_observation` 提交结构化证据评估。
- Runtime 不生成图片 safe summary，不替 runner 判断图片内容；Runtime 仍负责状态主权、附件鉴权、trace、snapshot 和 terminal output。
- 附件 bytes/base64 只在本次模型调用内存路径中流转，不进入 sandbox input、AgentRun persistence、TraceEvent、TerminalEvent、Replay 或 API response。

实现边界：

- 本阶段只支持终端上传的 `image/*` 作为 runner 多模态附件；audio/video 保持 metadata-only。
- `read_terminal_event_part` 只返回脱敏 metadata、attachment 可用性和 attachment source ref，不返回 bytes/base64。
- 附件引用使用 `terminal_event:<seq>:<part_id>`；evidence refs 只允许 terminal event 或 terminal part refs。
- 对象存储 key、内部 URL、credential 和 raw base64 不得暴露给模型工具、终端或持久化记录。

测试：

- 有图片 attachment 时，Runner 可以基于图片内容输出 `continue` / `complete`。
- 图片 artifact 缺失、对象存储不可用或超过附件上限时，Runtime 记录 `runtime.runner.attachment.warning`，Runner 要求重传或补充证据。
- prompt injection 文本、文件名、OCR/ASR 摘要中的越权指令不会改变工具权限或系统规则。

### Step 11：AgentRun 持久化与 Replay 关联

最小实现：

- `AgentHarnessService.invoke()` 在 Runtime 调用时传入 `persistence_session=session`。
- `persistence_context` 至少包含：
  - `related_skill_definition_id`
  - `related_job_id`，如果当前 runtime job 存在。
  - `related_runtime_run_id`
- Runtime trace payload 保存 `agent_run_id`，Replay 可通过 trace 定位 Agent timeline。
- Runtime runner 路径传入 `persistence_session=session`，但通过 `live_events_enabled=false` 关闭 live event sink，Agent events 在 agent 完成后一次性持久化，避免 Runtime 事务边界和 SQLite in-memory live session 冲突。

已补齐：

- 为 `AgentRunRecord`、repository、service、schema 增加 `related_runtime_run_id`。
- 增加索引 `idx_agent_run_related_runtime_run`。
- 如果当前项目仍使用 `create_schema()` 而非迁移系统，测试中直接创建新字段；后续如引入迁移，应补迁移脚本。

测试：

- Runtime runner 调用持久化 `agent_run` 与 `agent_event`。
- trace 中的 `agent_run_id` 可找到对应 AgentRunRecord。
- Replay timeline 展示 runtime decision，不展示隐藏推理。

### Step 12：清理旧 Runtime LLM prompt 依赖的边界

首版将 Runtime LLM / evidence evaluation 节点切到 `psop.runner` 路径，并收敛旧 prompt helper 的使用位置：

- 旧 `_render_llm_prompts()`、`_resolve_llm_attachments()`、`_parse_evaluation_observation()` 不再作为 Runtime LLM 节点的执行入口；如暂时保留，只作为历史兼容代码或后续清理对象。
- 新 Runner 路径不再要求模型直接输出 JSON 文本给 Runtime 解析，而是读取 `runner-observation.json`。
- 旧 trace event `gateway.inference.completed` 在 Runner 路径替换为 `runtime.agent.completed`，Agent 细节在 `agent_event` 中查看。
- `RUNTIME_LLM_LANGUAGE_POLICY` 的自然语言约束迁移到 runner `system.md` 和 output contract。

测试：

- Runtime 路径测试断言不生成旧 `gateway.inference.completed` trace。
- 新路径 terminal output 仍满足简体中文约束。

## 4. 分阶段交付建议

### Slice A：Agent Harness 独立 Runner

完成 Step 1 到 Step 6。

验收：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q tests/test_agent_harness_runner.py
PYTHONPATH=backend backend/.venv/bin/python tests/run_psop_runner_agent.py --fixture tests/fixtures/psop_runner/minimal.json --scripted
```

### Slice B：Runtime 文本路径接入

完成 Step 7、Step 8、Step 11 的最小实现和 Step 12。

验收：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q tests/test_runtime_services.py
```

重点覆盖：

- `instruct_*` terminal guidance 节点。
- `evaluate_*` evidence evaluation 节点。
- `need_more_evidence` / `retry` / `abort` / `complete`。
- `continue` 到 `proceed` 的兼容映射。

### Slice C：参考图片输出

完成 Step 9。

验收：

- 新增 runtime 测试覆盖 `terminal.multimodal.output.v1`。
- 对照 `docs/guides/terminal-integration-v1.md` 验证 parts 结构。

### Slice D：多模态证据语义补齐

状态：已完成。Step 10 的 Harness multimodal attachment 路径已落地。

验收：

- 有 image attachment 的图片证据可被 Runner 直接识别并接受或拒绝。
- 无法装配 image attachment 的媒体不会被模型当作确定事实，并产生 warning trace。

## 5. 完成定义

`psop.runner` 首版完成需要同时满足：

- `psop.runner` Agent 包、单一 `psop-runner` Skill 包及资源、runner tools、runner observation schema 和 required artifact contract 已实现。
- scripted runner agent 可独立生成合法 `runner-observation.json`。
- RuntimeService 的 LLM / evidence evaluation 节点默认走 Runner Agent，并保持 RuntimeService 状态主权。
- Runner observation 只作为普通 runtime observation 进入现有 merge、interaction、snapshot、trace、terminal output 和 halt 路径。
- Runtime LLM 节点不再保留启用开关或未注入 harness 的 direct LLM fallback。
- 终端上传图片可通过 Harness multimodal attachment 传给 Runner 直接识别；附件 bytes/base64 不进入 sandbox input、AgentRun persistence、TraceEvent、TerminalEvent、Replay 或 API response。
- pytest 全量通过。
- 新增脚本验收通过。
- Replay 可看到 Runtime decision 与 agent_run_id，AgentEvent 可审计工具、Skill 加载和 artifact 提交。

## 6. 非目标

- 不把 `RuntimeService.process_run()` 改写为 LangChain Agent 或 LangGraph。
- 不让 Runner 直接写 Session Token、TerminalEvent、Run、Invocation、TraceEvent、RuntimeJob、Skill source、EG artifact 或 runtime policy。
- 不新增 shell、开放网络、浏览器、数据库写工具、对象存储直读工具或任意 MCP connector。
- 不引入 subagents 或 long-running goal loop。
- 不在没有 runtime contract 支撑的情况下发明现场操作步骤。
- 不在没有可用 multimodal attachment 或可信证据的情况下把多媒体附件判定为已满足现场要求。
