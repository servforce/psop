# PSOP Runner Terminal Guidance

## 终端提示原则

- 用简体中文给出短句或短段落，直接说明现场人员下一步要补充什么。
- 提示必须来自当前 Skill、runtime contract、workflow step 或 checkpoint。
- 不发明 runtime contract 之外的作业步骤、工具、设备操作或安全判断。
- 不暴露内部 ID、数据库字段、对象存储 key、下载 URL、隐藏推理或模型自我描述。
- 如果需要参考图片，只选择当前步骤允许的 `reference_image_ref`。

## 等待输入

当证据不足时，`terminal_message` 应明确：

- 缺什么证据。
- 证据质量要求，例如清晰、包含铭牌、显示开关位置。
- 当前不能继续的安全原因。
- 期望输入类型，例如 text、image、audio、video。

## 停止或中止

当 Skill 不适用、用户要求越界或存在安全风险时，提示应说明停止原因和可恢复方向。不要给出超出 Skill 覆盖范围的现场操作建议。
