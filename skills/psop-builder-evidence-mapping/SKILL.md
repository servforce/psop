---
name: psop-builder-evidence-mapping
description: 当需要把 PSOP Skill draft 中的关键结论映射到用户描述、当前源码、素材分析、参考资产、行业标准和人工确认缺口时使用此 Skill。
allowed-tools:
  - psop.builder.list_materials
  - psop.builder.read_material_analysis
  - psop.builder.list_reference_assets
  - psop.standard.search
  - workspace.write_text
---

# PSOP Builder Evidence Mapping

## 使用场景

用于保证 builder 输出的每个关键结论可追溯，区分素材明确事实、行业标准参考、当前 source 事实、必要推断和人工确认缺口。

## 证据分层

- `observed_fact`：素材分析或参考资产直接支持。
- `standard_reference`：LightRAG 返回的标准片段支持。
- `current_source_fact`：当前 README/SKILL 已有内容。
- `builder_inference`：基于上下文的必要推断，只能低置信写入。
- `human_confirmation_required`：现有证据不足，必须进入 `missing_questions` 或 `review_notes`。

## 参考资产选择规则

1. 调用 `psop.builder.list_reference_assets` 获取候选资产。
2. 优先选择能帮助运行时判断状态、姿态、设备位置、缺陷、读数、工具摆放或安全边界的资产。
3. 不选择封面、转场、重复画面或无法支撑运行时判断的资产。
4. 每个 selected reference asset 必须说明 `used_in`，并在 `SKILL.md` 或 `references/README.md` 中被引用。
5. 如果没有可用参考资产，不得伪造路径，必须写入 `review_notes` 和 `missing_questions`。

## 行业标准映射规则

- 每个写入 draft 的标准性要求必须有可追溯来源。
- 如果标准片段只是背景知识，不能写成强制操作步骤。
- 如果标准和素材存在冲突，不得自行裁决，必须进入 `missing_questions` 或 `review_notes`。
- 当 `psop.standard.search` 返回 `citation_status="incomplete"` 时，只能作为 `reference_only` 使用或写入审阅说明。

## 建议中间产物

可用 `workspace.write_text` 写入：

- `/mnt/psop/workspace/evidence-map-draft.md`
- `/mnt/psop/workspace/reference-asset-selection.md`
- `/mnt/psop/workspace/standard-usage-draft.md`

这些中间产物只用于审阅和调试，最终仍以 `psop.builder.submit_candidate` 输入为准。
