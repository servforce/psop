# PSOP Compiler Quality Review

## 使用场景

用于在提交 compiler candidate 前做发布级自检，避免把无追溯来源、Runtime 不兼容或只包含通用模板的 EG candidate 提交给应用层。

## 自检清单

- artifact 顶层字段完整：`formal_revision`、`schema`、`nodes`、`init`、`halt`、`policies`、`dependency_graph_for_view`、`runtime_contract`。
- `formal_revision` 是 `psop-eg-formal/v5`。
- `nodes` 包含 start 和 terminal。
- 每个业务 workflow step 有 `instruct_<step_id>` 和 `evaluate_<step_id>`。
- 每个 evaluate/final_verify Prompt View 能看到必要 token 投影。
- `runtime_contract.workflow_steps`、`expected_evidence`、`wait_checkpoints`、`completion_criteria` 和 `recovery_paths` 可追溯到 source。
- `source_map` 覆盖 workflow steps、safety constraints、completion criteria、recovery paths 和关键业务节点。
- `dependency_graph_for_view` 不包含 artifact 中不存在或不可达的节点。
- policies 的预算按 workflow_steps 动态推导，不使用固定小上限。
- candidate 的形式控制结构优先来自 `psop.compiler.build_formal_v5_scaffold`，而不是模型手写的大 JSON。
- `psop.compiler.validate_formal_v5` 无 error；warning 必须进入 diagnostics 或 repair history。优先通过 `artifact_ref` 或 `candidate_ref` 校验 scaffold 产物。
- 提交前优先使用 scaffold 返回的 `candidate_ref`，避免在 tool arguments 中复制完整 EG JSON。

## 反模式

- 只生成通用 start/input/llm/tool/terminal 模板。
- workflow step id 使用 `step1`、`llm`、`tool` 等非业务语义命名。
- evaluate 节点不包含 token 投影却判断现场证据。
- dependency graph 添加没有 guard/merge/next_phase 支撑的 speculative edge。
- source evidence 不存在或只写“来自 SKILL.md”但没有片段或摘要。
- 为通过 validator 删除真实业务步骤。
- 把 domain pack 当作 formal-v5 或 runtime policy 事实源。
- 绕过 scaffold tool 手写大量重复的 guard、merge、interaction 和 wait checkpoint。

## 提交要求

调用 `psop.compiler.submit_candidate` 时，优先传入 scaffold 返回的 `candidate_ref`：

```json
{"candidate_ref": "sandbox://workspace/compiler-scaffold-candidate.json"}
```

如果必须直接传完整 candidate，参数必须包含：

- `artifact` 必须是 formal-v5 EG candidate。
- `compile_reason` 必须说明如何从 source 形成 EG。
- `source_map` 必须说明关键 target 的 source evidence。
- `diagnostics` 必须归档 source 缺口、不支持能力和风险。
- `repair_history` 必须记录 validator diagnostics 如何被修复；无修复时为空数组。
- `validator_summary` 必须来自最近一次 validator 结果摘要。

不得把 workspace 中间文件路径、自然语言总结或部分 JSON 当作最终 candidate。
