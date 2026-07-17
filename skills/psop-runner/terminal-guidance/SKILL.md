# PSOP Runner Terminal Guidance

## 上下文选择原则

- 提示必须来自当前 Skill、runtime contract、workflow step 或 checkpoint。
- 不发明 runtime contract 之外的作业步骤、工具、设备操作或安全判断。
- 不暴露内部 ID、数据库字段、对象存储 key、下载 URL、隐藏推理或模型自我描述。

## 等待输入

当证据不足时，先从上下文中确定：

- 已经收到或已经通过的部分。
- 缺什么证据。
- 证据质量要求，例如清晰、包含铭牌、显示开关位置。
- 当前不能继续的安全原因。
- 期望输入类型，例如 text、image、audio、video。

已经 `accepted` 的证据不再视为缺失。材料不可读或格式不可用时，在 observation 中准确记录原因和需要重传的材料类型。

## 停止或中止

当 Skill 不适用、用户要求越界或存在安全风险时，提示应说明停止原因和可恢复方向。不要给出超出 Skill 覆盖范围的现场操作建议。
