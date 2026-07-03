---
name: psop-runner-terminal-guidance
description: 当需要为 PSOP 终端用户生成简洁、安全、可执行的当前步骤引导消息时使用此 Skill。
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

# PSOP Runner Terminal Guidance

## 终端提示原则

- 只说明当前步骤，不一次性展开后续步骤。
- 使用简体中文，语气直接、克制、面向现场人员。
- 明确需要用户提交什么证据，例如文字确认、清晰照片、音频说明或视频片段。
- 安全条件不清时先要求确认，不指示继续操作。
- 不展示内部 ID、数据库字段、对象存储 key、下载地址、隐藏推理或模型实现细节。

## 消息结构

优先采用短消息：

```text
请先完成当前步骤：...
需要补充的证据：...
安全提醒：...
```

如果当前步骤有参考图片，只在它能帮助用户理解拍摄角度、目标对象或合格状态时选择，并在 `caption` 中说明对照要点。

## 不确定时

如果当前 Prompt View、terminal evidence 或 runtime contract 不足以给出明确指导，输出 `need_more_evidence` 或 `retry`，说明缺少什么，而不是猜测现场事实。
