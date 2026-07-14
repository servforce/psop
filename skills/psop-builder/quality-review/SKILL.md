# PSOP Builder Quality Review

## 使用场景

用于在提交 builder candidate 前做发布级自检，避免把普通教程、素材摘要、占位内容或不可追溯结论写入 Skill draft。

## 自检清单

- `files` 包含所有 required files，且没有 `skill.yaml`。
- `README.md` 说明 Skill 目标、适用范围、输入素材要求、输出和审阅注意事项。
- `SKILL.md` 包含阶段化 workflow、证据门、等待条件、安全停止和恢复路径。
- `prompts/system.md` 只包含运行时必要系统提示，不混入 builder 工作日志。
- `references/README.md` 能说明参考资产和行业标准的用途。
- 候选文档中引用参考图片时必须使用可解析的 `reference_path`；哪个流程步骤使用该图片，就必须在该步骤附近用 Markdown 图片语法引用相对路径，例如 `![CPU 安装参考](references/video-keyframes/.../000950504.jpg)`。`submit_candidate` 会把选中的原图文件物化到最终 PSOP Skill draft 的 `references/` 目录，不能使用 base64 data URI，不能要求用户打开外部图片链接，也不能把参考图片集中放在文档底部。
- `examples/input.md` 与 `examples/expected-output.md` 展示典型调用和期望行为。
- `tests/checklist.md` 覆盖 happy path、缺失证据、风险停止、标准引用和人工确认。
- `evidence_map` 中每个关键 claim 都有合法 source refs。
- `workflow_step_candidates` 和 `expected_evidence_requirements` 能对应 `SKILL.md` 的阶段。
- `industry_standard_usage` 中的标准引用可追溯，没有被写成未受支持的强制要求。

## 发布审阅标准

候选产物的目标状态是“可进入人工发布审阅的 draft”，提交前必须满足：

- `README.md` 描述目的、范围、输入、输出和维护注意事项，不暴露 builder 实现细节。
- `SKILL.md` 包含完整阶段化 workflow、前置条件、动作、证据、等待点、安全约束、恢复路径和完成标准。
- `examples/expected-output.md` 与 `SKILL.md` 使用一致的阶段编号、阶段名称和协作行为。
- `SKILL.md` 在使用参考图片的具体流程步骤中引用 `selected_reference_assets.reference_path`，并使用相对 Markdown 图片链接。
- 最终 draft 的参考图片文件保存在 `references/` 目录，通过相对链接展示，不能使用 base64 data URI。
- 不出现 TODO、占位路径、省略号路径、无法证实的硬件规格或未来能力承诺。
- `review_notes` 明确列出素材缺口、不确定推断和需要人工确认的事项。
- 所有 `selected_reference_assets.reference_path` 都必须在 `SKILL.md` 流程内容中出现，文档不得引用未选中的参考资产路径。

## 反模式

- 把 PSOP Skill 写成操作说明书摘要，没有运行时判断条件。
- 每一步都写“确认安全”，但不说明确认什么证据。
- 引用标准但没有标准编号、条款、适用位置。
- 只选择漂亮关键帧，不选择能支撑运行时判断的关键帧。
- 把素材中不确定或被遮挡的动作写成确定事实。
- 产物中出现 `TODO`、`待补充`、`根据实际情况` 等未审阅占位内容。
- 把核心运行时行为只放在 `prompts/system.md`，而 `SKILL.md` 中缺少对应契约。
- 将素材转写成长篇教程、营销文案、品牌口播或普通文章。
- 用户未提供关键证据时，workflow 仍一次性推进多个物理阶段。

## 提交要求

提交前必须确认候选产物可以通过 `psop.builder.submit_candidate` 的结构、路径、引用和追溯校验。`submit_candidate` 成功只表示候选产物通过第一层校验，不表示 GitLab draft 已提交。

调用 `psop.builder.submit_candidate` 时，参数必须直接包含完整 candidate：

- `files` 中必须有所有必需 Markdown 文件的完整内容。
- `directory_tree`、`generation_reason`、`material_usage`、`evidence_map`、`safety_constraints`、`workflow_step_candidates`、`expected_evidence_requirements` 必须同时提交。
- `selected_reference_assets` 最多 12 项，并且每个 `reference_path` 必须在 `SKILL.md` 的使用步骤附近以 Markdown 图片链接形式出现。
- 如果 LightRAG 检索失败，不要伪造行业标准；在 `review_notes` 中说明失败状态，并保持 `industry_standard_usage` 为空数组或只写可追溯的 `reference_only` 项。

不得把 workspace 中的 `submit-params.json`、证据映射草稿或参考资产选择草稿当作最终候选产物。
