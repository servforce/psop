# Skill Creator Acceptance Checklist

## Directory Completeness

- [ ] 输出包含完整 Skill 目录树
- [ ] 输出包含 `SKILL.md`
- [ ] 输出包含 `skill.yaml`
- [ ] 输出包含 `prompts/system.md`
- [ ] 输出包含 `references/README.md`
- [ ] 输出包含 `examples/input.md`
- [ ] 输出包含 `examples/expected-output.md`
- [ ] 输出包含 `tests/checklist.md`

## Draft Quality

- [ ] `SKILL.md` 清楚描述 Skill 的目标、场景、步骤、约束与产物
- [ ] `skill.yaml` 与 `SKILL.md` 的工作流定义一致
- [ ] `prompts/system.md` 能支撑该 Skill 的实际执行方式
- [ ] `examples/` 能说明如何使用该 Skill
- [ ] `tests/checklist.md` 能说明如何验证该 Skill

## Standard Conformance

- [ ] 使用 `SKILL.md`，没有写成 `Skill.md`
- [ ] 使用 `references/`，没有写成 `knowledge/`
- [ ] `skill_code` 符合小写字母、数字、连字符规则
- [ ] 输出形态是“可审阅草稿”，而不是只有概述

## Benchmark Validation

- [ ] 能够用于创建 `skill-builder`
- [ ] 创建 `skill-builder` 时，输出足以支持后续继续进入编译链路设计
