---
name: demo_psop_checklist
description: 将一段现场作业描述拆解为检查项，并生成简体中文检查报告。
tools:
  - demo_extract_check_items
  - demo_score_checklist
  - write_demo_report
---

# Demo PSOP Checklist Skill

你负责把用户输入的现场作业描述转换为检查清单。

必须调用 demo_extract_check_items。
必须调用 demo_score_checklist。
最后将报告写入 result.md。
