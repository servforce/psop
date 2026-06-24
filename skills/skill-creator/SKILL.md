---
name: skill-creator
description: 通过多轮需求澄清生成一个完整可审阅的 Skill 草稿目录。
allowed-tools: []
---

# Skill Creator

`skill-creator` 用于在 VS Code Codex 中，通过多轮需求澄清，生成一个完整可审阅的 Skill 草稿目录。

它的目标不是只写一份说明文档，而是产出一套符合当前项目标准的 Skill 草稿，至少包含：

- `SKILL.md`
- `skill.yaml`
- `prompts/system.md`
- `references/`
- `examples/`
- `tests/`

## Purpose

通过结构化问答，帮助用户定义一个新的 Skill，并输出一份完整的目录草稿，供用户审阅、修改、确认后再落盘或继续进入后续编译链路。

当前阶段的核心验收标准是：

- 输出目录结构符合 `skills/<skill-code>/` 标准
- 输出内容足以构成一个完整可审阅的 Skill 草稿
- 能够创建出 `skill-builder` 这样的下游生产型 Skill

## When To Use

在以下场景使用本 Skill：

- 用户想创建一个新的 Skill
- 用户只有目标和场景，尚未整理出完整的 Skill 文件结构
- 用户希望先得到一份可审阅的完整 Skill 草稿
- 用户希望创建的 Skill 未来可以进入编译或执行链路

在以下场景不优先使用本 Skill：

- 只需要微调已有 Skill 的局部文案
- 只需要补一个示例文件或一个测试清单
- 已经有完整目录，只需要做增量维护

## Required Outcome

运行结束时，必须给出：

1. Skill 目录树
2. 每个关键文件的完整草稿内容
3. 用户需要重点审阅的检查项

输出不得停留在“想法”或“提纲”层面。

## Workflow

### Step 1. Clarify Goal

先澄清待创建 Skill 的核心目标，至少收集：

- `name`
- `skill_code`
- `purpose`
- `target_user`
- `scenarios`

如果信息不足，继续追问，不要直接开始写文件。

### Step 2. Clarify Execution Shape

继续澄清该 Skill 应如何工作，至少收集：

- `inputs`
- `outputs`
- `workflow`
- `constraints`

这里的重点是明确这个 Skill 到底如何完成任务，以及它不能越过哪些边界。

### Step 3. Clarify Validation Assets

收集用于形成完整 Skill 草稿的配套内容：

- `references_needed`
- `examples_needed`
- `acceptance_checks`

如果用户没有明确说明，也要基于目标给出合理的默认草稿。

### Step 4. Generate Review Draft

在信息足够后，一次性输出完整 Skill 草稿目录，至少包含：

- `SKILL.md`
- `skill.yaml`
- `prompts/system.md`
- `references/README.md`
- `examples/input.md`
- `examples/expected-output.md`
- `tests/checklist.md`

输出应按“目录树 -> 分文件内容 -> 审阅提示”的顺序组织。

## Output Rules

- 输出必须明确文件路径
- 输出必须给出完整文件内容，而不是摘要
- 输出默认是“可审阅草稿”，不默认直接落盘
- 如果用户要求落盘，应在草稿生成后单独执行

## Directory Standard

目标 Skill 必须符合以下目录结构：

```text
skills/<skill-code>/
├─ SKILL.md
├─ skill.yaml
├─ prompts/
│  └─ system.md
├─ references/
│  └─ README.md
├─ examples/
│  ├─ input.md
│  └─ expected-output.md
└─ tests/
   └─ checklist.md
```

## Hard Rules

- `skill_code` 只能使用小写字母、数字与连字符
- 不要生成缺少关键文件的半成品目录
- 不要只输出自然语言介绍而省略结构化文件
- 不要把 `references/` 写成 `knowledge/`
- 不要把 `SKILL.md` 写成 `Skill.md`
- 不要在信息明显缺失时强行生成看似完整但实际空泛的草稿

## Review Checklist

在输出草稿后，提醒用户重点检查：

- Skill 目标是否准确
- 输入与输出定义是否清晰
- 工作步骤是否合理
- 约束是否完整
- 示例是否能说明预期结果
- 测试清单是否足以验证该 Skill

## First Target Example

本 Skill 的第一优先验证对象是创建 `skill-builder`。

也就是说，`skill-creator` 的输出能力至少要足以产出一个面向后续编译链路的完整 Skill 草稿，而不只是一个演示性文案样例。
