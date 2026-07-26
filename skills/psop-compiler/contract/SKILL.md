# PSOP Compiler Formal-v5 Contract

## 使用场景

用于把 PSOP Skill 的业务 workflow 约束为当前 Runtime 支持的 formal-v5 PSOP-EG。此资源是形式定义和 validator 不变量的执行指南，不替代 deterministic validator。

## 首选生成路径

不要直接手写完整 EG 大 JSON。必须按以下路径生成：

1. 从 frozen `SKILL.md` / `README.md` 抽取业务 `workflow_steps`。
2. 为每个 step 给出稳定英文 `id`、中文 `title`、`goal`、`source_evidence` 和 `expected_evidence`。
3. 调用 `psop.compiler.build_formal_v5_scaffold`，让工具机械生成 nodes、guards、merges、wait checkpoints、dependency view、`artifact_ref` 和 `candidate_ref`。
4. 调用 `psop.compiler.validate_formal_v5` 校验 scaffold 返回的 `artifact_ref` 或 `candidate_ref`。
5. 校验通过后，把 scaffold 返回的 `candidate_ref` 传给 `psop.compiler.submit_candidate`。

不要在 `validate_formal_v5` 或 `submit_candidate` 的 tool arguments 中复制完整 EG 大 JSON，除非 validator diagnostics 要求做局部修复且无法复用引用。

## workflow_steps 输入要求

每个 workflow step 至少包含：

```json
{
  "id": "precheck_compatibility",
  "title": "预检与兼容性确认",
  "goal": "确认 CPU、主板、电源、机箱和防静电准备满足装机要求。",
  "source_evidence": "SKILL.md 阶段 1 描述了硬件清单、电源功率、机箱规格和防静电准备。",
  "expected_evidence": {
    "requirements": [
      {
        "requirement_key": "compatibility_check",
        "description": "确认配置清单和兼容性自查结果。",
        "required": true,
        "evidence_options": [
          {"option_key": "text_attestation", "kind": "text", "event_kind": "terminal.text.input.v1", "proof_mode": "attestation"}
        ]
      },
      {
        "requirement_key": "power_label_visual",
        "description": "图片清晰显示电源额定功率标签。",
        "required": true,
        "evidence_options": [
          {"option_key": "label_photo", "kind": "image", "event_kind": "terminal.multimodal.input.v1", "proof_mode": "visual"}
        ]
      }
    ]
  },
  "source_file": "SKILL.md"
}
```

`id` 使用小写英文和下划线，不使用 `start`、`input`、`llm`、`tool`、`terminal`、`final`、`finish` 等模板节点名。

新产物的 `runtime_contract.evidence_contract_version` 固定为 `psop-evidence/v2`。一个 requirement 对应一个待证明事实；同一事实的替代证据放入 `evidence_options`。图片不足时允许文字确认，应表现为同一 requirement 的 image/text 两个 option，而不是两个 required requirement。需要不同证明方式的复合句必须拆分；例如螺丝是否存在是 visual requirement，是否手动达到 `snug fit` 是 attestation requirement。

## formal-v5 顶层不变量

artifact 必须包含：

- `formal_revision = "psop-eg-formal/v5"`
- `schema`
- `nodes`
- `init`
- `halt`
- `policies`
- `dependency_graph_for_view`
- `runtime_contract`

`runtime_contract` 必须包含：

- `execution_goal`
- `applicability`
- `workflow_steps`
- `expected_evidence`
- `safety_constraints`
- `wait_checkpoints`
- `completion_criteria`
- `recovery_paths`

## 节点不变量

每个 workflow step 必须展开为两个节点：

- `instruct_<step_id>`
- `evaluate_<step_id>`

每个 `instruct_<step_id>` 必须满足：

- `kind = "llm"`
- `actor.name = "agent.llm"`
- `guard = {"phase_is": "instruct_<step_id>"}`
- `interaction.output_to_terminal = true`
- 首个 instruct 的 `interaction.runner_turn_kind = "first_step_instruction"`
- 其余 instruct 的 `interaction.runner_turn_kind = "step_instruction"`
- `interaction.wait_after_output = true`
- `interaction.resume_phase = "evaluate_<step_id>"`
- `interaction.expected_inputs` 非空
- `projection.user_template` 非空且包含 `{{token}}`
- `merge` 必须包含 `{"op":"set","path":"observations.instruct_<step_id>","from":"observation"}`

每个 `evaluate_<step_id>` 必须满足：

- `kind = "llm"`
- `actor.name = "agent.llm"`
- `guard = {"phase_is": "evaluate_<step_id>"}`
- `interaction.evaluation = true`
- `interaction.runner_turn_kind = "evidence_evaluation"`
- `projection.user_template` 非空且包含 `{{token}}`
- `merge` 必须包含 `{"op":"set","path":"observations.evaluate_<step_id>","from":"observation"}`
- `merge` 必须能根据评估结果设置下一阶段，例如 `{"op":"set","path":"phase","from":"observation.next_phase"}`

图中必须包含：

- `start`
- 每个 step 的 `instruct_*` 和 `evaluate_*`
- `final_verify`
- `terminal`

`final_verify.interaction.runner_turn_kind` 必须是 `final_verification`。回合类型只约束 Runner 的表达任务，不改变 guard、wait checkpoint、transition、merge 或 Runtime 状态主权。

## guard / merge 约束

guard 只使用：

- `always`
- `phase_is`
- `field_exists`
- `field_equals`
- `all`
- `any`
- `not`

merge 只使用：

- `op = "set"`
- `path` 的顶层字段只能来自 allowed runtime token fields，例如 `phase`、`observations`、`outputs`、`status`、`control`、`facts`、`metadata`。
- 每个 merge operation 必须且只能包含 `value` 或 `from`。

## validator diagnostics 修复映射

- `workflow step 缺少指令节点 instruct_<id>`：不要手写补丁，优先重新调用 `build_formal_v5_scaffold`，确保 step id 与节点 id 一致。
- `workflow step 缺少证据评估节点 evaluate_<id>`：重新调用 scaffold tool，或补齐 `evaluate_<id>`。
- `必须把 observation 写入 observations.<node_id>`：给对应 instruct/evaluate 节点添加固定 merge。
- `resume_phase 必须指向 evaluate_<id>`：把 instruct 节点的 `interaction.resume_phase` 改成对应 evaluate 节点。
- `wait_checkpoints 缺失`：每个 workflow step 增加一个 `checkpoint_id=<step_id>_evidence` 的 wait checkpoint。
- `字段路径引用未知 Token 顶层字段`：只使用 allowed runtime 的 token fields，优先改为 `observations.*`、`outputs.*`、`phase` 或 `status`。
- `MVP merge 仅支持 op=set`：把 `assign`、`write`、`append` 等改为 `set`，必要时把聚合逻辑放入 LLM 输出。

## 提交原则

validator 返回 error 时不要调用 `submit_candidate` 伪装成功。应先使用 diagnostics 修复；如果两轮仍失败，提交失败诊断而不是删除真实业务 workflow。
