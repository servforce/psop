# Example Input

请帮我创建一个新的 Skill：`skill-builder`。

要求如下：

- 用途：把一个标准 Skill 目录编译成可供后续运行时消费的最小运行产物草稿
- 目标用户：维护 PSOP Skills 的开发者
- 输入：
  - 一个符合标准的 `skills/<skill-code>/` 目录
  - 其中至少包含 `SKILL.md`、`skill.yaml`、`prompts/`、`references/`、`examples/`、`tests/`
- 输出：
  - 一个最小编译产物草稿
  - 一份构建结果说明
- 工作流：
  - 检查目录结构
  - 校验关键文件是否存在
  - 读取 `skill.yaml`
  - 生成最小 runtime bundle 草稿
  - 输出构建说明
- 约束：
  - 当前阶段先不执行复杂脚本
  - 先不处理工具调用
  - 先以最小可验证编译链路为目标

请先澄清缺失信息，再输出完整 Skill 草稿目录。
