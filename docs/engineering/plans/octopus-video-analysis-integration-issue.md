# [Feature] PSOP Skill 视频素材分析接入 Octopus MCP

## 背景

PSOP 当前支持用户在 Skill 页面上传视频，并对视频进行语音转写、关键帧提取和内容分析，随后由 Builder 根据分析结果生成 Skill 草稿。

Octopus 也具备视频内容分析能力，可以从视频中识别文本和关键画面，并且把选取每个步骤最关键的一帧并且分段落保存，能够更精确的提供视频关键帧信息并生成 Markdown 形式的分析结果。Octopus 目前正在优化，其对外能力后续将通过 MCP 提供。

本期希望将 PSOP 的视频素材分析流程与 Octopus MCP 打通：用户仍在 PSOP 中上传视频，由 Octopus 完成视频分析并产出 Markdown 和相关图片，再将这些结果交给现有 Builder 继续生成 Skill 草稿。

## 当前实现与约束

- PSOP 已有视频上传、素材保存、后台解析和 Skill 草稿生成流程。
- 当前视频解析主要由 PSOP 内部完成，Octopus 尚未接入正式流程。
- Octopus 正在改造为 MCP 服务，本期只描述 PSOP 与 Octopus 协作的业务流程。
- Octopus 生成的 Markdown 是视频分析材料，不是可以直接发布的 `SKILL.md`。
- Skill 的生成、校验、预览和发布仍由 PSOP 现有 Builder 与发布流程负责。
- 用户上传的视频以及 Octopus 返回的分析结果需要能够关联和追溯。

## 核心判断

Octopus 与 PSOP 在该流程中的职责不同：

```text
Octopus
  -> 理解视频内容
  -> 提取文字和关键画面
  -> 输出 Markdown 分析结果

PSOP
  -> 管理用户上传的素材和任务状态
  -> 通过 MCP 使用 Octopus 能力
  -> 保存并展示分析结果
  -> 使用 Builder 生成最终 Skill 草稿
```

因此，Octopus 的输出应作为 Builder 的素材输入，而不能直接替代最终 Skill。PSOP 通过 Octopus MCP 使用视频分析能力，但 Octopus MCP 本身的设计不属于本 Issue。

## 目标

1. 用户继续在 PSOP Skill 页面上传视频，不改变主要使用入口。
2. PSOP 后端能够通过 MCP 将视频交给 Octopus 分析。
3. PSOP 能获取并保存 Octopus 返回的 Markdown 和相关关键图片。
4. Octopus 的分析结果可以替换当前内部视频分析结果，作为 Builder 的主要素材输入。
5. Builder 继续根据分析材料生成符合 PSOP 规范的 Skill 草稿。
6. 用户可以看到视频分析的执行状态、结果和失败原因。
7. 整个过程保留原视频、分析结果与 Skill 草稿之间的关联关系。

## 非目标

- 本期不将 Octopus Markdown 直接发布为 PSOP Skill。
- 本期不绕过 Builder、Compiler、人工确认和现有发布流程。
- 本期不在 PSOP 中重新实现 Octopus 已提供的视频识别能力。
- 本期不包含 Octopus MCP 本身的设计和开发。
- 本期不修改 PSOP Runtime 和黑盒时序测试流程。

## 用户流程

1. 用户进入 Skill 页面并上传视频。
2. PSOP 保存视频并创建视频分析任务。
3. PSOP 通过 Octopus MCP 提交视频分析请求。
4. Octopus 完成视频内容识别，生成 Markdown 和相关图片。
5. PSOP 获取并保存分析结果，在页面中向用户展示。
6. 用户确认素材分析结果后，使用现有入口生成 Skill 草稿。
7. Builder 根据 Octopus 分析结果生成可继续编辑和发布的 Skill。

## 功能要求

- PSOP 统一使用 Octopus MCP 的视频分析能力，用户不需要感知系统之间的协作过程。
- 视频分析应继续使用后台任务，避免长时间处理阻塞用户页面。
- PSOP 应能区分分析中、分析完成和分析失败等基本状态。
- Markdown 中使用的关键图片应随分析结果一并纳入 PSOP 管理，避免最终 Skill 依赖不可控的临时地址。
- 同一视频重复处理时，应避免产生相互冲突的结果。
- Octopus 分析失败时，不应覆盖已有的成功结果，也不应生成内容不完整的 Skill 草稿。

## 安全与边界

- PSOP 只启用本功能需要的 Octopus MCP 能力。
- Octopus 返回的内容属于外部分析结果，进入 Builder 前仍需经过 PSOP 的校验和人工确认。
- 原始视频与分析图片继续遵守 PSOP 现有的对象存储和访问控制方式。

## 验收标准

- [ ] 用户可以在现有 Skill 页面上传视频并启动 Octopus 分析。
- [ ] PSOP 可以通过 Octopus MCP 完成视频分析。
- [ ] PSOP 可以获取并展示 Octopus 生成的 Markdown 和关键图片。
- [ ] 分析结果能够作为 Builder 输入并生成 Skill 草稿。
- [ ] 最终 Skill 仍经过现有校验、预览和发布流程。
- [ ] Octopus 输出不会被直接当作最终 `SKILL.md` 发布。
- [ ] 页面可以正确展示分析中、成功和失败状态。
- [ ] Octopus 分析失败不会破坏原始视频或已有成功结果。

## 测试要求

### 后端

- 验证视频可以通过 Octopus MCP 提交并取得分析结果。
- 验证 Markdown 和图片能够正确保存并关联到原始素材。
- 验证失败、超时和重复执行不会产生错误的 Skill 草稿。
- 验证 Builder 可以读取 Octopus 分析结果并保持原有校验流程。

### 前端

- 验证用户可以沿用现有入口上传视频并查看处理状态。
- 验证分析成功后可以预览结果并生成 Skill 草稿。
- 验证分析失败时能够看到可理解的提示并重新尝试。

### 端到端

使用一段步骤清晰的物理操作视频，验证以下完整流程：

```text
上传视频
  -> Octopus MCP 分析
  -> 返回 Markdown 和关键图片
  -> PSOP 保存并展示
  -> Builder 生成 Skill 草稿
```

## 文档同步

- 更新视频素材分析和 Skill 构建流程说明。
- 更新 PSOP 与 Octopus MCP 的职责边界说明。

## 完成定义

用户在 PSOP 上传视频后，PSOP 能通过 Octopus MCP 获得 Markdown 和关键图片，将其作为可追溯的视频分析材料交给现有 Builder，并最终生成符合 PSOP 流程的 Skill 草稿；Octopus 负责视频理解，PSOP 仍负责素材管理、Skill 构建、校验和发布。
