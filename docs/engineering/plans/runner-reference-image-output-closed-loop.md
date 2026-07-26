# Runner 参考图片输出闭环实施计划

> 状态：历史计划，已被当前 Runner 纯文本输出策略取代。Compiler 仍保留参考图片资产与 runtime contract 索引，但 `psop.runner` 已移除参考图选择、observation 字段和 `terminal.multimodal.output.v1` 新增输出逻辑；现行行为以系统架构、Runner 详细设计和终端接入文档为准。

## 背景

历史实现中，Runtime 已支持 `terminal.multimodal.output.v1`，Runner observation 也有 `reference_images` 字段，但编译产物没有稳定提供当前步骤参考图。结果是 Runner 没有可选图片，终端自然只收到纯文本输出。

本计划把参考图片输出实现为正式运行时契约链路：

```text
Skill source references/
  -> Compiler 镜像为 ArtifactObject
  -> runtime_contract.workflow_steps[*].reference_images
  -> Runtime 提供当前步骤候选图
  -> Runner 只选择 reference_image_ref
  -> Runtime 校验并输出 terminal.multimodal.output.v1
```

## 实施范围

- 不新增数据库表，不新增对外 REST API。
- 复用现有 `ArtifactObject`、对象存储、`runtime_contract`、Runner observation 和 terminal part content endpoint。
- Compiler 只解析 `README.md`、`SKILL.md` 中的 Markdown 图片链接。
- 合法图片必须是相对路径、位于 `references/` 目录下，并使用受支持图片扩展名。
- Runtime 不在运行时回读 GitLab source；发布编译后的运行只依赖数据库和对象存储中的受控 artifact。

## 已实现链路

1. Compiler 在发布编译时解析 frozen source 的合法参考图片链接。
2. Compiler 通过 GitLab gateway 按 frozen commit 读取图片 bytes。
3. Compiler 将图片上传到对象存储，创建 `ArtifactObject`，并生成 `source.reference_assets`。
4. `psop.compiler.read_skill_source` 向 compiler agent 暴露只读 `reference_assets`。
5. `psop.compiler.build_formal_v5_scaffold` 保留 `workflow_steps[].reference_images`，写入 `runtime_contract.workflow_steps[*].reference_images[]`。
6. Runtime 调用 Runner 时只传当前步骤候选图。
7. Runner observation 只需要提交 `reference_image_ref`。
8. Runtime 输出前用当前步骤候选图补齐并校验 `artifact_object_id`，不信任 Runner 自带对象 ID。
9. 有效图片输出 `terminal.multimodal.output.v1`；无有效 artifact 时退化文本并追加 `runtime.runner.reference_image.warning`。

## 验收用例

- 带 `![现场概览](references/site-overview.jpg)` 的 Skill 发布编译后，首个 workflow step 含 `reference_images[]` 且每项有有效 `artifact_object_id`。
- 外部 URL、data URI、非 `references/` 路径、路径越界和非图片文件不会进入 `source.reference_assets`。
- 发布带参考图的 Skill 后运行，Runner 选择合法 ref 时终端收到 `terminal.multimodal.output.v1`，其中包含 text part 和 image part。
- Runner 提交不存在的 `reference_image_ref` 会被 observation 校验拒绝。
- Runner 提交合法 ref 但伪造 `artifact_object_id` 时，Runtime 使用 runtime contract 中的对象 ID。
- 无参考图的 Skill 仍输出 `terminal.text.output.v1`，不要求终端新增协议。

## 非目标

- 不支持运行时按需读取 GitLab 图片。
- 不支持 Runner 上传临时图片作为参考图。
- 不改变终端展示协议或 part content endpoint。
- 不把 `references/README.md` 作为本次默认解析入口；后续如需要支持，可扩展 Compiler 资产索引规则。
- 不新增图片编辑、缩略图生成、CDN 分发或跨 Skill 资产复用能力。
