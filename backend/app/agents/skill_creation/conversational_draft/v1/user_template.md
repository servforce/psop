请根据以下输入构建用于 AI 协助人类完成现实任务的 Skill 草稿。

请重点关注：
- 用户想创建的 PSkill 目标
- 当前 PSkill source 中可复用、需要替换或必须保留的内容
- prompt payload 中的 psop_skill_form_definition、physical_world_skill_guidance 和 publishable_document_skill_standard
- 素材中的任务对象、现场状态、任务步骤、判断标准、安全约束、异常情况和协作参考知识
- 哪些内容必须进入自包含的 SKILL.md，哪些内容仅作为 prompts/system.md、references/、examples/ 的辅助材料
- 哪些素材事实可以直接使用，哪些是必要推断，哪些需要人工确认
- 任务是否存在型号、版本、材料、工具、环境或现场状态差异；如存在，必须建模为确认条件、分支路径或停止条件
- AI 运行时应如何等待用户证据、判断是否继续、要求补充证据或暂停/终止

请输出完整 JSON object。
