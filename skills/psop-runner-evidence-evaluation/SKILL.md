---
name: psop-runner-evidence-evaluation
description: 当需要评估 PSOP 终端提交的文本、图片、音频或视频证据是否满足当前 workflow step 时使用此 Skill。
allowed-tools:
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
---

# PSOP Runner Evidence Evaluation

## 证据判断

证据充分必须同时满足：

- 与当前 workflow step 和 checkpoint 相关。
- 类型符合 `expected_inputs`。
- 内容足以支撑当前步骤完成或继续推进。
- 没有明显安全风险、对象不匹配、顺序异常或质量不足。

用户声称“已经完成”不自动等于证据充分。图片模糊、对象不完整、音视频摘要不清、缺少关键安全确认时，输出 `need_more_evidence` 或 `retry`。

## Evidence Assessment

在 `evidence_assessment` 中区分：

- `accepted_event_refs`：可采信的终端事件。
- `rejected_event_refs`：不满足要求的终端事件。
- `missing_evidence`：还需要用户补充的具体证据。
- `unsafe_or_ambiguous_facts`：安全风险或无法确认的现场事实。

`terminal_event:<seq_no>` 必须来自当前工具返回的终端事件，不能引用未来事件或编造事件。

## 安全优先

如果断电、隔离、防护、设备状态、现场环境或权限不清，不要指示用户继续高风险操作。输出安全提醒，并请求补充证据或建议中止。
