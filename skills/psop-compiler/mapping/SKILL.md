# PSOP Compiler Formal-v5 Mapping

## 使用场景

用于把 PSOP Skill 中的现实作业阶段映射为 formal-v5 Execution Graph。此资源只提供映射方法，不授予额外工具权限。

## 映射规则

- 首选做法是抽取业务 workflow steps 后调用 `psop.compiler.build_formal_v5_scaffold`，不要直接手写完整 nodes、guards、merges 和 dependency graph。
- scaffold 返回 `artifact_ref` / `candidate_ref` 时，后续 `validate_formal_v5` 和 `submit_candidate` 必须优先传引用，不要复制完整 EG JSON。
- `start` 节点只初始化 Session Token，不承载业务判断。
- 每个业务 workflow step 生成 `instruct_<step_id>` 和 `evaluate_<step_id>`。
- `instruct_<step_id>` 面向用户输出当前现实步骤指令，设置 wait checkpoint，并等待现场证据。
- `evaluate_<step_id>` 消费用户证据和 token，输出 `proceed`、`retry`、`need_more_evidence`、`abort` 或 `complete`。
- `final_verify` 必须在成功 terminal 前验证 completion criteria。
- `terminal` 只在成功或失败终止条件满足时写入最终状态和输出。
- guard 只能使用 `psop.compiler.read_allowed_runtime` 返回的 guard DSL。
- merge 只能做受控写入，不得写入未声明或不属于 Session Token 的路径。
- `dependency_graph_for_view` 只表达 artifact 中真实可达边，不能加入没有 guard/merge/next_phase 支撑的 speculative edge。

## scaffold 输入映射

把 Skill source 中每个阶段映射为 `build_formal_v5_scaffold.workflow_steps[]`：

- `id`：小写英文语义 ID，例如 `precheck_compatibility`、`install_motherboard_components`。
- `title`：Skill 中阶段标题。
- `goal`：阶段完成目标。
- `source_evidence`：来自 SKILL.md/README.md 的阶段摘要或片段。
- `expected_evidence`：阶段要求用户提交的照片、截图、文字确认或文件。
- `source_file`：通常为 `SKILL.md`。
- `reference_images`：可选。只能从 `psop.compiler.read_skill_source` 返回的 `reference_assets` 中选择与该阶段相关的图片。每项包含 `reference_image_ref`、`title`、`caption`、`artifact_object_id`、`mime_type`、`source_ref`、`display_order`；`artifact_object_id` 必须来自 `reference_assets`，不能由模型编造。

参考图片映射语义：

- `reference_assets` 是发布编译阶段已镜像到对象存储的只读资产索引，不是 Runner 临时附件。
- `workflow_steps[].reference_images[].reference_image_ref` 是运行时 Runner 选择图片时使用的稳定引用。
- 图片 bytes、base64、对象存储 key、内部 URL 不进入 runtime contract。
- 如果无法确定某张图片适用于哪个步骤，不要强行映射；可以在 diagnostics 中说明 source 参考图缺少步骤语义。

## Prompt View 要求

- 指令型 LLM 节点输出用户可见的简体中文 terminal message。
- 评估型 LLM 节点必须只输出 JSON object。
- evaluate / final_verify 节点必须在 projection 中暴露 `{{token}}` 或等价 token 投影。
- JSON 字段名和 decision、next_phase 等协议枚举值保持英文，reason、terminal_message 等自然语言字段值使用简体中文。

## 建议中间产物

可使用 `workspace.write_text` 写入：

- `/mnt/psop/workspace/workflow-step-map.md`
- `/mnt/psop/workspace/node-phase-map.md`
- `/mnt/psop/workspace/source-map-draft.md`
- `/mnt/psop/workspace/validator-repair-notes.md`

这些文件只用于调试和审阅，最终仍以 `psop.compiler.submit_candidate` 的参数为准；scaffold 返回的 `candidate_ref` 可以作为正式 submit 参数。
