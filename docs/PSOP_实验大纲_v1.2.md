# PSOP 实验大纲 v1.2

## 1. 总览：实验命题、双任务组合与核心链路

### 1.1 核心实验命题

本实验要证明的不是“AI 能不能回答问题”，而是：

> **PSOP 能否把复杂现实任务或复杂手册驱动任务，先描述为 PSkill，再编译成可执行 PSOP-EG，并通过 Runner 的状态管理、证据检查、主动补证、Final Verify、Tester 和 World Model，使不同能力水平的用户更稳定、更快、更安全地完成目标。**

### 1.2 双主实验的互补性

| 主实验 | 实验简称 | 证明对象 | 任务性质 | 关键成功证据 |
|---|---|---|---|---|
| 3D 打印小备件 | PrintOps-Physical | PSOP 能否把真实需求推进到物理成品 | 低成本、真实物理世界任务 | 成品照片、尺寸测量、装配测试、final verify |
| SHENZHEN I/O 完成 puzzle | RTFM-Digital | PSOP 能否把复杂手册和工程规则转化为状态化指导 | 确定性 RTFM 数字工程任务 | spec 抽取、manual 引用、simulation trace、pass screenshot、方案解释 |

两个任务组合在一起比单独做游戏或单独做真实设备更强：

1. **3D 打印任务证明现实世界闭环**
   - 用户必须完成测量、建模、切片、打印、后处理、装配测试。
   - 任务有真实材料成本、设备状态、物理误差和安全边界。
   - 成功不能只靠语言判断，必须由实体成品和证据验证。
2. **SHENZHEN I/O 任务证明 RTFM 与状态化推理**
   - 用户必须阅读 puzzle spec 和 manual。
   - 任务有确定性 simulation 反馈。
   - 成功不能只靠“看起来合理”，必须由游戏 pass 和方案解释验证。

### 1.3 PSOP 的不可替代链路

实验必须坚持一个原则：**PSOP-EG 必须由 PSkill 编译生成，不能把 EG 当成手写流程图。**

```text
PSkill source
  → Skill Operational Contract
  → Compiler
  → Compiler-generated PSOP-EG artifact
  → Validator
  → Tester + World Model hardening
  → Recompile / freeze EG revision
  → Runner executes EG
  → Session Token / Trace / Evidence / Result
```

对应交付物不是“两份 PSkill + 两份人工 EG”，而是：

1. **3D 打印链路**
   - `PSkill-Desktop3DPrint-FunctionalSparePart`
   - `Compiler-generated PSOP-EG for Desktop3DPrint`
   - `Tester + WorldModel suite for Desktop3DPrint`
   - 冻结后的 `Desktop3DPrint-FunctionalSparePart@rev-xxx`
2. **SHENZHEN I/O 链路**
   - `PSkill-SHENZHENIO-ManualDrivenPuzzleCompletion`
   - `Compiler-generated PSOP-EG for SHENZHEN I/O`
   - `Tester + WorldModel suite for SHENZHEN I/O`
   - 冻结后的 `SHENZHENIO-ManualDrivenPuzzleCompletion@rev-xxx`
3. **问题修复原则**
   - 如果 tester 发现漏洞，优先修改：
     - `PSkill source`
     - `compiler mapping rule`
     - `validator rule`
     - `world model scenario spec`
   - 然后重新编译 EG。
   - 不直接手改 EG，避免破坏 PSkill 作为 source of truth 的地位。

### 1.4 研究问题与假设矩阵

| 编号 | 要回答的问题 | 对应假设 | 主要指标 | 预期方向 |
|---|---|---|---|---|
| Q1 | PSOP 是否比 Manual / Generic AI 提高任务成功率？ | H1 | SuccessRate | PSOP Basic > Manual / Generic AI；PSOP Full > PSOP Basic |
| Q2 | PSOP 是否降低低能力用户完成复杂任务的门槛？ | H2 | AbilityThreshold_80、低能力组成功率 | 低能力用户在 PSOP 条件下提升最大 |
| Q3 | PSOP 是否减少 false success？ | H3 | FalseSuccessRate | PSOP < Generic AI |
| Q4 | PSOP-EG 的状态管理是否使任务更稳定推进？ | H4 | 状态收敛时间、关键节点到达率 | PSOP 更快、更稳定到达关键状态 |
| Q5 | Evidence、Active Probe、Final Verify 是否减少跳步和伪完成？ | H4 / H5 | 证据完整率、final verify 失败率 | PSOP Full 最优 |
| Q6 | Tester + World Model hardening 是否带来增益？ | H5 | PSOP Basic vs Full 差异 | Full 错误率更低、证据更完整 |
| Q7 | PSOP 的优势是否同时出现在物理任务和数字工程任务中？ | H1-H5 | ToolCondition × TaskFamily 交互 | 两类任务都有效，效果大小允许不同 |

### 1.5 核心假设的操作化表达

| 假设 | 操作化表达 | 解释 |
|---|---|---|
| H1：PSOP 提高任务成功率 | `SuccessRate(PSOP Basic) > SuccessRate(Manual)`；`SuccessRate(PSOP Basic) > SuccessRate(Generic AI)`；`SuccessRate(PSOP Full) > SuccessRate(PSOP Basic)` | 证明 PSOP 不只是聊天提示，而是结构化执行系统 |
| H2：PSOP 降低能力门槛 | `低能力用户 + PSOP Full ≈ 中能力用户 + Generic AI`，甚至 `> 中能力用户 + Manual` | 证明 PSOP 对新手最有价值 |
| H3：PSOP 降低 false success | `FalseSuccessRate(PSOP) < FalseSuccessRate(Generic AI)` | 3D 打印中防止尺寸/装配伪成功；SHENZHEN I/O 中防止未 pass 却声称完成 |
| H4：状态管理有效 | 关键状态更快、更稳定到达 | 3D 打印关注测量、建模、切片、打印、装配、final verify；SHENZHEN I/O 关注 spec、manual、simulation、diagnosis、pass |
| H5：Tester + World Model 有增益 | `PSOP Full` 比 `PSOP Basic` 更低错误率、更高证据完整率 | 证明持续改进飞轮，而不只是一次性流程设计 |

---

## 2. 实验设计：工具条件、能力分组与样本量

### 2.1 四个工具条件

| 条件 | 用户可用资源 | 包含的机制 | 明确不包含 | 实验意义 |
|---|---|---|---|---|
| Condition A：Manual | 静态说明书、任务说明、安全提示、验收标准 | 无交互、无状态管理 | 无 AI、无证据 checkpoint、无 final verify | 代表传统 SOP / 文档指导 |
| Condition B：Generic AI | 固定一个普通 AI 模型，可自由对话 | 自然语言问答、通用建议 | 无 PSkill、无 compiled EG、无 Session Token、无强制 evidence checkpoint、无 active probe、无 tester hardening | 代表自由聊天式 AI 指导 |
| Condition C：PSOP Basic | PSkill 编译出的 PSOP-EG | Session Token、instruct/evaluate 节点、evidence requirement、wait checkpoint、retry / need_more_evidence / abort、final verify、基础 LLM evaluator | 无大规模 tester hardening、无 world model 反例强化、无 experience ledger 优化 | 证明 PSkill → EG → Runner 的基础价值 |
| Condition D：PSOP Full | Hardened PSOP-EG | Basic 全部机制，加 positive / negative / reverse / counterfactual tester、scripted world model、修订后的 PSkill / compiler rule、重新编译后的 hardened EG、初步 experience ledger | 不允许直接手改 EG | 证明 PSOP 的持续改进飞轮 |

### 2.2 不同任务下 Manual 的具体材料

1. **3D 打印任务**
   - 任务说明。
   - 打印机快速入门。
   - 切片软件基础说明。
   - 安全提示。
   - 验收标准。
2. **SHENZHEN I/O 任务**
   - 游戏内 puzzle spec。
   - 游戏内 manual。
   - 实验任务说明。

### 2.3 能力评分与分组

实验保留 30 / 60 / 90 的直觉分组，但形式化为 100 分能力评分。

| 维度 | 分值 | 说明 | 对 3D 打印的相关性 | 对 SHENZHEN I/O 的相关性 |
|---|---:|---|---|---|
| 通用理解与问题分解 | 20 | 阅读说明、拆解任务、识别关键条件 | 中 | 高 |
| 数字工具能力 | 20 | 手机、电脑、文件上传、截图、软件操作 | 中 | 高 |
| 动手与测量能力 | 20 | 卡尺、装配、观察缺陷、设备操作 | 高 | 低 |
| 编程 / 逻辑 / 电路基础 | 20 | 条件逻辑、状态追踪、简单程序理解 | 低 | 高 |
| 安全与规范意识 | 20 | 是否会停下来检查风险、保留证据 | 高 | 中 |

| 分组 | 分数范围 | 描述 | 预期 PSOP 增益 |
|---|---:|---|---|
| 低能力组 | 0-40 | 基本无相关经验 | 最大 |
| 中能力组 | 41-70 | 有基础工具和学习能力 | 中等 |
| 高能力组 | 71-100 | 有工程、编程、设备、3D 打印或电子基础 | 可能主要体现在减少错误和提升证据质量 |

分析时不只看总分，也看子维度：

- 3D 打印更依赖：
  - 动手与测量能力；
  - 安全与规范意识；
  - 基础数字工具能力。
- SHENZHEN I/O 更依赖：
  - 通用理解与问题分解；
  - 编程 / 逻辑 / 电路基础；
  - manual 阅读与状态追踪。

### 2.4 样本量设计

| 版本 | 设计公式 | 人数 | 每人任务 | 总 run 数 | 主要目的 |
|---|---|---:|---|---:|---|
| Pilot | `3 能力组 × 4 工具条件 × 2 人` | 24 | 1 个 3D 打印任务 + 1 个 SHENZHEN I/O puzzle | 48 | 校准流程、任务难度、评分标准和 PSOP-EG 稳定性 |
| 主实验 | `3 能力组 × 4 工具条件 × 5 人` | 60 | 同上 | 120 | 产出第一版严肃白皮书、内部验证报告或 arXiv 初稿 |
| 增强版 | `3 能力组 × 4 工具条件 × 8 人` | 96 | 同上 | 192 | 支持能力门槛曲线和 mixed-effects 统计模型 |

Pilot 重点不追求统计显著，而是验证：

1. **任务可控性**
   - 打印时间是否合理。
   - SHENZHEN I/O puzzle 是否过难或过易。
   - 任务 variant 是否公平。
2. **工具条件可比性**
   - 用户是否理解 PSOP。
   - PSOP-EG 是否卡死。
   - Generic AI 是否直接给出完整答案。
3. **测量系统可靠性**
   - 评分标准是否清晰。
   - 专家评分是否一致。
   - 证据提交是否足够支撑 final verify。

### 2.5 随机化与执行规则

- **任务顺序随机化**
  - 一半用户先做 3D 打印。
  - 一半用户先做 SHENZHEN I/O。
- **工具条件固定**
  - 一个用户在两个任务中使用同一工具条件。
  - 避免用户在不同工具之间迁移策略。
- **任务 variant 随机分配**
  - 3D 打印可以在同类小备件参数上随机化。
  - SHENZHEN I/O 可以在 2-3 个难度相近 puzzle 中随机化。
- **实验环境固定**
  - 固定模型版本、工具版本、PSkill revision、compiler version、EG revision。
  - 禁止外部搜索，或统一设置联网条件。

---

## 3. 主实验 A：3D 打印小备件

### 3.1 实验定位

| 项目 | 内容 |
|---|---|
| 实验名称 | **PrintOps-Physical: Functional Spare Part Manufacturing** |
| 对应 PSkill | `PSkill-Desktop3DPrint-FunctionalSparePart` |
| 编译产物 | `Desktop3DPrint-FunctionalSparePart@rev-xxx` |
| 核心目标 | 用户根据简单真实需求，完成小型功能性备件的制造闭环 |
| 第一阶段建议任务 | 线缆固定卡扣 |
| 不建议第一阶段目标 | 高强度承重件、医疗件、食品接触件、电气安全关键件、精密工业件 |

3D 打印小备件比 FoodPrint 更适合作为主实验，因为它具备：

1. **真实功能性**
   - 成品需要装配或夹持。
   - 不是纯展示物。
2. **成本可控**
   - 单件耗材少。
   - 失败可记录、可重复。
3. **评价明确**
   - 有尺寸阈值。
   - 有装配测试。
   - 有照片 / 视频证据。
4. **可连接后续商业闭环**
   - 第一阶段用实验室内桌面打印机控制变量。
   - 第二阶段可扩展到线上 3D 打印服务平台模拟下单或真实下单。

### 3.2 3D 打印任务流

```text
理解需求
  → 测量尺寸
  → 选择 / 生成参数化模型
  → 检查可打印性
  → 切片
  → 打印
  → 后处理
  → 测量成品
  → 装配测试
  → 提交证据
  → final verify
```

按 PSOP 状态拆分为：

1. **需求与边界确认**
   - 理解备件用途。
   - 判断是否属于低风险、非承重、非医疗、非食品接触任务。
   - 超出适用边界时 abort / escalate。
2. **测量与参数确认**
   - 测量线缆、轴、安装板或孔距。
   - 记录单位，统一为 mm。
   - 使用卡尺照片和数值截图作为证据。
3. **建模与可打印性检查**
   - 选择 OpenSCAD 参数化模板。
   - 输入测量值生成 STL / 3MF。
   - 检查壁厚、孔径、尺寸、支撑需求和平台边界。
4. **切片与打印执行**
   - 固定 slicer、profile、材料和打印机。
   - 保存 slicer project、G-code hash、切片截图。
   - 记录首层照片和打印完成照片。
5. **成品验证**
   - 测量关键尺寸。
   - 执行装配 / 夹持 / 扭转测试。
   - 提交 final evidence。
   - 由 final verify 判断成功、失败或需要补证。

### 3.3 第一阶段任务梯度

| 任务 | 阶段 | 目标 | 用户关键动作 | 难点 | 成功标准 |
|---|---|---|---|---|---|
| Task A：线缆固定卡扣 | Pilot 首选 | 为指定直径线缆和固定位置打印功能性卡扣 | 测量线缆直径、测量固定位置厚度、输入参数、生成模型、切片、打印、安装测试、提交证据 | 单位、夹持间隙、壁厚、首层质量 | 线缆能放入；能夹持；不明显松脱；不易断裂；关键尺寸误差在阈值内；证据完整；final verify 通过 |
| Task B：旋钮替代件 | 第二阶段 | 为标准轴打印可用旋钮 | 测量轴径、设置孔径公差、打印、套轴、旋转测试 | 轴径测量、孔径公差、防滑、扭转测试 | 能套上轴；不开裂；能旋转；不严重打滑；尺寸合理 |
| Task C：传感器 / 小模块安装支架 | 第三阶段 | 为小传感器、摄像头模块或开发板打印安装支架 | 测量孔距、确定方向、切片、安装 | 孔距、安装方向、螺丝孔、支撑、稳定性 | 能安装；方向正确；孔位可用；结构稳定；证据完整 |

### 3.4 3D 打印证据与 guard 矩阵

| 阶段 | 必要证据 | 关键 guard | 失败 / 补证路径 |
|---|---|---|---|
| 需求确认 | 任务描述、用途说明 | 高风险用途必须 abort / escalate | 用户改选低风险任务或由实验员处理 |
| 测量 | 测量值、卡尺照片、单位说明 | 没有测量值不能生成模型；测量值异常必须重新测量 | need_more_evidence / retry_measurement |
| 建模 | 参数截图、模型预览、STL / 3MF 文件 | 模型不可打印不能切片；壁厚过薄不能进入打印 | 修改参数或模板 |
| 切片 | 切片截图、slicer profile、材料、预计时间、G-code hash | 切片参数缺失不能打印 | 补充截图或重新切片 |
| 打印 | 打印开始照片、首层照片、完成照片 | 首层失败必须 retry 或 abort | 记录失败，允许按规则重试 |
| 后处理与测量 | 成品照片、关键尺寸测量照片 | 没有成品照片不能 final verify | 补拍或重新测量 |
| 装配测试 | 装配照片或视频、测试结果 | 没有装配测试不能 success | 补做 fit test |
| Final Verify | 全部证据包 | 证据不完整、功能失败、安全违规都不能 success | terminal_failure / need_more_evidence / escalation |

### 3.5 3D 打印机与实验环境选型

3D 打印实验的目标不是考验用户调机能力，而是验证 PSOP 能否管理“需求到成品”的状态化制造流程。因此设备优先级是：**稳定、低成本、低噪声、低维护、可重复、证据可采集。**

#### 3.5.1 第一阶段只选 FDM / FFF，不选光固化

| 维度 | FDM / FFF | 树脂光固化 | 第一阶段结论 |
|---|---|---|---|
| 耗材成本 | PLA / PETG 便宜 | 树脂和后处理耗材更复杂 | FDM 更合适 |
| 安全门槛 | 相对低 | 涉及酒精清洗、UV 固化、树脂废液 | 避免引入安全变量 |
| 后处理 | 去支撑、去毛刺即可 | 清洗、固化、废液处理 | FDM 更可控 |
| 功能小备件 | 适合 | 更适合高精细模型 | FDM 与任务目标更匹配 |
| 失败可观察性 | 首层、翘曲、断裂直观 | 失败机制更难解释给新手 | FDM 更适合用户实验 |

#### 3.5.2 设备类别与定位

| 类别 | 代表设备 | 优点 | 风险 | 推荐定位 |
|---|---|---|---|---|
| 新手友好型高稳定打印机 | Bambu Lab A1 mini / A1 | 上手快、自动化程度高、适合低能力用户、首层和校准问题少 | 生态相对封闭；云端 / 账号 / 远程控制可能引入变量 | 第一阶段主设备 |
| 开源 / 可审计友好型打印机 | Original Prusa MINI+ / Prusa MK 系列 | 生态开放、文档清晰、适合作为研究环境、利于复现 | 对完全新手可能不如 Bambu 省心；采购维护成本可能更高 | Reference printer / reproducibility branch |
| 低成本高可及性打印机 | Creality Ender-3 V3 SE 及同级机型 | 成本低、用户群大、接近普通创客环境 | 调机变量更强，可能污染主实验 | 第二阶段 robustness test |

#### 3.5.3 推荐设备策略

1. **Pilot 主设备**
   - 使用 2-3 台同型号新手友好型 FDM 打印机。
   - 推荐：`Bambu Lab A1 mini` 或 `Bambu Lab A1`。
   - 选择规则：
     - 所有备件都小于 120 mm：优先 A1 mini。
     - 需要更大支架、治具或多件排布：优先 A1。
   - 固定变量：
     - 固件版本。
     - Bambu Studio 版本。
     - 耗材品牌和批次。
     - 打印 profile。
     - 是否联网、是否启用自动检测功能。
2. **Reference / 复现实验设备**
   - 增加 1 台 Prusa MINI+ 或类似 open/reproducible 设备。
   - 作用：
     - 验证 PSOP 不只适用于单一品牌。
     - 支持论文中的 reproducibility。
     - 对比封闭生态和开放生态。
     - 测试 PrusaSlicer / 本地切片流程。
3. **第一阶段不建议使用**
   - 树脂打印机。
   - 大尺寸打印机。
   - 高速 CoreXY 高级机。
   - 需要大量手动调平的老式打印机。
   - 多喷头 / 多材料复杂设备。
   - 改装机或维护状态不稳定的二手机。

#### 3.5.4 工具链、材料与附件

| 类别 | 第一阶段选择 | 第二阶段扩展 | 选择理由 |
|---|---|---|---|
| CAD / 参数化建模 | OpenSCAD 参数化模板 | FreeCAD | 参数清楚、易记录、tester 可生成尺寸变体、PSOP 可验证参数合理性 |
| 切片软件 | Bambu 设备用 Bambu Studio；Prusa / 通用设备用 PrusaSlicer | 固定多 slicer 对照 | 固定工具链，降低版本变量 |
| 文件格式 | OpenSCAD source、STL、3MF、slicer project file、G-code、final photo/video、measurement photo、fit-test evidence | 增加在线平台订单文件 | 保留完整可追溯链路 |
| 耗材 | PLA 或 PLA+ | PETG | 低气味、低翘曲、低安全风险 |
| 暂不使用材料 | ABS、ASA、Nylon、碳纤增强材料、TPU、高温材料、食品接触材料、阻燃 / 绝缘功能材料 | 后续专项实验 | 避免气味、翘曲、干燥、喷嘴磨损、安全和工艺变量 |
| 颜色 | 统一灰色或白色 | 可按视觉检测需求扩展 | 便于拍照和视觉评估 |

必须准备的附件分为四类：

1. **测量与装配**
   - 数显卡尺。
   - 标准测试轴。
   - 标准线缆。
   - 标准螺丝。
   - 标准安装板。
2. **后处理与安全**
   - 去毛刺工具。
   - 小螺丝刀。
   - 尖嘴钳。
   - 防烫提示牌。
   - 灭火器或消防毯。
3. **证据采集**
   - 摄像头 / 手机支架。
   - 实验台编号牌。
   - 零件标签贴纸。
4. **成本与废料记录**
   - 耗材称重设备。
   - 废料盒。

### 3.6 3D 打印 PSkill、EG 与 Tester 设计

#### 3.6.1 PSkill contract

| 模块 | 内容 |
|---|---|
| Goal | 用户制造一个低风险、小尺寸、非承重、非医疗、非食品接触的功能性小备件，并通过装配验证 |
| Applicability：适用 | 小型塑料备件；PLA / PETG 可打印；非关键承重；非高温环境；非电气安全关键件；可通过简单尺寸和装配测试验证 |
| Applicability：不适用 | 医疗器械；食品接触件；高温环境；车辆 / 安全结构件；高强度承重件；电气绝缘关键件；法规管制零件；需要精密公差的工业件 |
| Workflow Steps | `understand_requirement → check_scope_and_safety → collect_measurements → validate_measurements → select_template → generate_model → check_model_printability → choose_material → choose_orientation → slice_model → review_slicer_settings → start_print → monitor_first_layer → complete_print → post_process → measure_final_part → fit_test → final_verify` |
| Evidence Requirements | 测量值、卡尺照片、参数截图、模型预览、切片截图、打印开始照片、首层照片、成品照片、尺寸测量照片、装配测试照片或视频 |
| Completion Criteria | 成品存在；尺寸符合；功能通过；证据完整；没有安全违规；final verify 通过 |

#### 3.6.2 Compiler-generated PSOP-EG 节点组

| 节点组 | EG 节点 |
|---|---|
| 启动与边界 | `start_printops_task`；`understand_requirement`；`verify_scope_and_safety` |
| 测量 | `instruct_measurements`；`evaluate_measurements` |
| 建模 | `generate_model_from_template`；`evaluate_model_printability` |
| 切片 | `instruct_slicing`；`evaluate_slicer_settings` |
| 打印 | `instruct_print_start`；`evaluate_first_layer`；`wait_for_print_completion` |
| 成品验证 | `evaluate_final_part_photo`；`instruct_measure_final_part`；`evaluate_final_measurements`；`instruct_fit_test`；`evaluate_fit_test` |
| 终局 | `final_verify_part`；`terminal_success`；`terminal_failure_or_escalation` |

#### 3.6.3 Tester 与 World Model

| 测试类型 | 生成内容 | 检查目标 |
|---|---|---|
| Positive cases | 用户测量正确、参数合理、模型可打印、切片正确、打印成功、装配成功、final verify 成功 | 正常路径能否顺利完成 |
| Negative cases | 未测量、直径/半径混淆、mm/cm 混淆、孔径小于轴径、壁厚过薄、模型超出平台、切片无支撑、首层失败、成品无法装配、照片模糊、用户要求跳过检查、声称成功但无证据 | guard 是否阻止跳步和伪成功 |
| Reverse cases | `success_without_measurement`、`success_without_final_photo`、`success_without_fit_test`、`unsafe_part_accepted`、`wrong_dimension_accepted`、`failed_print_marked_success` | 从坏状态反推最短违规路径，找 EG 漏洞 |
| Scripted World Model | 用户能力、测量误差、单位错误、打印失败、首层失败、孔径偏小、支撑缺失、材料不合适、漏拍照片、过早声称完成 | 模拟现实错误分布，生成测试场景和 belief/proposal |

World Model 的边界：

- 只生成 test scenarios 和 belief/proposal。
- 不能写 facts。
- 不能绕过 final verify。

---

## 4. 主实验 B：SHENZHEN I/O 完成 puzzle

### 4.1 实验定位

| 项目 | 内容 |
|---|---|
| 实验名称 | **RTFM-Digital: Manual-Driven Circuit Puzzle Completion** |
| 对应 PSkill | `PSkill-SHENZHENIO-ManualDrivenPuzzleCompletion` |
| 编译产物 | `SHENZHENIO-ManualDrivenPuzzleCompletion@rev-xxx` |
| 核心目标 | 用户在不直接复制完整答案的条件下，基于 puzzle spec 和 manual 完成指定 puzzle |
| 主实验模式 | Coaching Mode |
| 补充模式 | Completion Mode |

SHENZHEN I/O 适合作为 RTFM 环境，因为它要求用户构建电路、编写代码、阅读 datasheets / reference guides / technical diagrams，并通过 simulation 验证。

### 4.2 任务模式选择

| 模式 | AI / PSOP 可做 | AI / PSOP 不可做 | 适用位置 | 证明对象 |
|---|---|---|---|---|
| Coaching Mode | 指导用户理解 spec、阅读 manual、抽取 IO、选择组件、调试、分析失败 | 不能直接给完整最终电路和完整代码 | 第一主实验 | PSOP 是否增强人的 RTFM 和工程执行能力 |
| Completion Mode | 可以给出完整方案，用户实现并验证 | 仍需 final verify 和 pass screenshot | 补充实验 | 端到端完成能力 |

主实验使用 Coaching Mode 的原因：

1. 公开 puzzle 可能已被大模型记住。
2. 直接给答案会把实验变成“模型记忆测试”。
3. Coaching Mode 更能验证 PSOP 的状态化指导、证据管理和调试闭环。

### 4.3 Puzzle 选择原则

选择 puzzle 时应满足：

1. **难度适中**
   - 不太简单。
   - 不太难。
   - 30-60 分钟内有机会完成。
2. **需要 RTFM**
   - 输入输出明确。
   - 需要定位 manual section。
   - 需要理解组件行为或时序。
3. **反馈可验证**
   - 有 simulation feedback。
   - 可以截图保存失败和成功状态。
   - 不以极致优化为目标，只看能否正确完成。
4. **降低记忆污染**
   - 避免网络上最常见的标准解。
   - 记录用户是否玩过 Zachtronics 游戏。
   - 后续可自建 SHENZHEN-like puzzle。

### 4.4 SHENZHEN I/O 任务流、证据与 guard 矩阵

| 阶段 | 用户动作 | 必要证据 | 关键 guard |
|---|---|---|---|
| 读题 | 阅读 puzzle spec | puzzle spec screenshot | 没有 spec screenshot 不能进入 final evidence |
| 规格抽取 | 提取输入、输出、时序、约束 | 用户提取的 IO 要求 | 没有 spec extraction 不能进入 design |
| 定位 manual | 找相关 manual section | manual section 截图或引用 | 没有 manual reference 不能 claim understanding |
| 方案草案 | 选择组件、描述电路策略 | strategy text、组件列表 | AI 不得直接给完整最终电路和代码 |
| 搭建与编码 | 放置组件、接线、写初版代码 | 电路截图、代码截图 | 使用不存在组件 / 指令必须拦截 |
| simulation | 运行测试 | simulation result 截图 | 没有 simulation result 不能 final verify |
| 失败诊断 | 收集 failure trace、定位原因 | failed trace、诊断说明 | simulation failed 不能 success |
| 修正与复跑 | 修改电路或代码并复测 | revision screenshot、rerun result | partial pass 不能 success |
| 通过与解释 | 提交 pass screenshot、解释核心逻辑 | pass screenshot、方案解释 | 没有 pass screenshot 或解释不能 full success |
| Final Verify | 汇总证据 | 全部证据包 | 违反 coaching policy、证据缺失或未 pass 均失败 |

### 4.5 SHENZHEN I/O PSkill、EG 与 Tester 设计

#### 4.5.1 PSkill contract

| 模块 | 内容 |
|---|---|
| Goal | 用户在不直接复制完整答案的条件下，基于 puzzle spec 和 manual 完成指定 SHENZHEN I/O puzzle |
| Applicability：适用 | 官方 puzzle；用户可访问游戏内 manual；用户可截图；用户可运行 simulation；用户可提交失败 trace 和最终 pass screenshot |
| Applicability：不适用 | 需要外部解法数据库；需要隐藏游戏机制；需要 speedrun；需要最优 score；用户已经完整玩过该 puzzle |
| Workflow Steps | `read_puzzle_spec → extract_io_requirements → identify_manual_sections → summarize_relevant_components → draft_solution_strategy → select_components → place_components → wire_inputs_outputs → write_initial_code → run_simulation → collect_failure_trace → diagnose_failure → revise_circuit_or_code → rerun_simulation → pass_all_tests → explain_solution → final_verify` |
| Evidence Requirements | puzzle spec screenshot、用户提取的输入输出要求、相关 manual section、电路截图、代码截图、simulation 失败截图、失败诊断说明、最终 pass screenshot、用户方案解释 |
| Completion Criteria | 游戏显示 puzzle pass；提交 pass screenshot；用户能解释核心逻辑；过程没有直接复制完整答案；final verify 通过 |

#### 4.5.2 Compiler-generated PSOP-EG 节点组

| 节点组 | EG 节点 |
|---|---|
| 启动与读题 | `start_shenzhen_task`；`instruct_read_spec`；`evaluate_spec_understanding` |
| 手册定位 | `instruct_identify_manual_sections`；`evaluate_manual_references` |
| 规格抽取 | `instruct_extract_io_requirements`；`evaluate_io_requirements` |
| 方案设计 | `instruct_draft_strategy`；`evaluate_strategy` |
| 搭建与编码 | `instruct_build_circuit`；`evaluate_circuit_screenshot`；`instruct_write_code`；`evaluate_code_snapshot` |
| 运行与诊断 | `instruct_run_simulation`；`evaluate_simulation_result`；`diagnose_failure` |
| 修订与复验 | `instruct_revision`；`evaluate_revision` |
| 终局 | `final_verify_puzzle`；`terminal_success`；`terminal_failure_or_timeout` |

#### 4.5.3 Tester 与 World Model

| 测试类型 | 生成内容 | 检查目标 |
|---|---|---|
| Positive cases | 正确抽取 IO、正确引用 manual、初始方案部分正确、失败后提交 trace、PSOP 引导诊断、用户修复、最终通过 | 正常 coaching 路径能否推动到 pass |
| Negative cases | 没读 spec、IO 理解反了、使用不存在 instruction、组件接线错误、代码只过部分测试、simulation failed 却声称完成、只上传代码截图、AI 编造 manual 内容、用户要求跳过解释 | 防止 hallucination、跳步、伪成功 |
| Reverse cases | `success_without_pass_screenshot`、`success_after_failed_simulation`、`manual_hallucination_accepted`、`io_spec_misunderstood_but_design_allowed`、`direct_solution_policy_violated` | 从坏状态反推 guard 漏洞 |
| Scripted World Model | 用户没读 manual、误解时序、接线错误、只会改代码不会看 trace、以为 partial pass 完成、AI 给出不存在组件、AI 忽略 puzzle 约束、用户上传错误截图 | 模拟常见认知错误和证据错误 |

---

## 5. 统一执行流程与数据采集

### 5.1 受试者执行流程

| 顺序 | 步骤 | 产出 / 记录 | 说明 |
|---:|---|---|---|
| 1 | 签到与同意书 | consent record | 统一实验说明 |
| 2 | 能力测评 | ability_score、ability_group、子维度分 | 作为分组和协变量 |
| 3 | 工具条件随机分配 | tool_condition | Manual / Generic AI / PSOP Basic / PSOP Full |
| 4 | 工具培训 | training completion | 只教工具使用，不教任务答案 |
| 5 | 任务 1 | run record、trace、evidence | 任务顺序随机化 |
| 6 | 任务后问卷 | satisfaction、difficulty、trust、cognitive load | 记录主观体验 |
| 7 | 休息 | break record | 降低疲劳影响 |
| 8 | 任务 2 | run record、trace、evidence | 使用同一工具条件 |
| 9 | 任务后问卷 | 同上 | 两任务分别记录 |
| 10 | 半结构化访谈 | interview notes | 收集失败原因和机制解释 |
| 11 | 专家评分 | expert_score、success、false_success | 统一 rubric |
| 12 | 数据清洗 | clean dataset | 对齐版本、证据和时间戳 |

### 5.2 数据采集 schema

| 数据表 | 关键字段 | 用途 |
|---|---|---|
| Participant dataset | `participant_id`、`ability_score`、`ability_group`、五项能力子分、过往经验 | 分组、协变量、能力门槛分析 |
| Run dataset | `participant_id`、`tool_condition`、`task_family`、`task_variant`、`start_time`、`end_time`、`success`、`completion_time`、`expert_score`、`false_success`、`intervention_count` | 主分析表 |
| Survey dataset | `user_satisfaction`、`perceived_difficulty`、`cognitive_load`、`trust`、`willingness_to_reuse` | 用户体验分析 |
| PSOP trace dataset | `PSkill revision`、`compiler version`、`EG revision`、`Session Token snapshots`、`trace events`、`node enabled/ready/selected`、`evidence accepted/rejected`、`active probe count`、`final verify result`、`terminal events`、`model calls`、`token usage`、`tool usage`、`tester suite version`、`world model scenario version` | 机制分析与复现 |
| 3D print artifact dataset | `printer_id`、`printer_model`、`firmware_version`、`slicer_version`、`slicer_profile`、`filament_type`、`filament_batch`、`part_type`、`model_template_version`、`parameter values`、`STL hash`、`G-code hash`、`print_time`、`filament_grams`、`print_failure`、`measurement_error`、`fit_test_result`、`final_part_score`、`photos/videos` | 物理任务质量、成本和失败归因 |
| SHENZHEN artifact dataset | `puzzle_id`、`puzzle_difficulty`、`mode`、`manual_sections_used`、`simulation_runs`、`failed_runs`、`circuit_revision_count`、`code_revision_count`、`final_pass`、`pass_screenshot`、`explanation_score`、`direct-answer-policy violations` | RTFM 任务质量、调试成本和策略合规性 |

### 5.3 版本与证据追踪规则

每个 run 都必须保存：

1. **PSOP 版本**
   - PSkill revision。
   - Compiler version。
   - EG revision。
   - Tester suite version。
   - World Model scenario version。
2. **3D 打印 artifact 版本**
   - CAD template version。
   - Parameter values。
   - STL hash。
   - Slicer version。
   - Slicer profile。
   - G-code hash。
   - Printer ID。
   - Filament ID / batch。
3. **SHENZHEN I/O artifact 版本**
   - Puzzle ID。
   - Puzzle variant。
   - Manual section references。
   - Circuit revision snapshots。
   - Code revision snapshots。
   - Simulation result screenshots。

---

## 6. 评价指标与数据分析方案

### 6.1 核心评价指标

| 指标 | 公式 / 定义 | 3D 打印判定 | SHENZHEN I/O 判定 | 解释重点 |
|---|---|---|---|---|
| 任务成功率 | `SuccessRate = 成功 run 数 / 总 run 数` | 专家验收 + final verify 通过 | pass screenshot + 方案解释 + final verify 通过 | 主效果 |
| 完成时间 | `CompletionTime = EndTime - StartTime`，未完成按 timeout 处理 | 从任务开始到 final verify | 从读题开始到 final verify | 不能脱离质量单独解释 |
| False Success Rate | `FalseSuccessRate = 声称成功但专家/系统判定失败的 run 数 / 声称成功 run 数` | 尺寸错误、无法装配、证据缺失却声称完成 | 未 pass、无 pass screenshot、无法解释却声称完成 | 证明治理能力的关键指标 |
| 证据完整率 | `EvidenceCompleteness = 已提交必要证据 / 应提交必要证据` | 测量、模型、切片、打印、成品、装配证据 | spec、manual、simulation、pass、解释证据 | 衡量 evidence-driven execution |
| 状态收敛时间 | 到达关键状态的时间戳 | `T_measurements_validated`、`T_model_generated`、`T_slicer_reviewed`、`T_print_completed`、`T_fit_test_passed`、`T_final_verified` | `T_spec_extracted`、`T_manual_identified`、`T_initial_design_created`、`T_simulation_run`、`T_failure_diagnosed`、`T_all_tests_passed`、`T_final_verified` | 证明状态管理是否有效 |
| 返工 / 调试次数 | 重复关键步骤次数 | 重新测量、重新生成模型、重新切片、重新打印、装配失败 | simulation run、failed simulation、circuit revision、code revision | 衡量执行摩擦 |
| 成本指标 | 时间、材料、token、交互轮数等 | 耗材克数、打印时间、失败材料浪费、设备占用时间、人工干预次数 | 用户时间、AI token、交互轮数、无效建议次数 | 衡量真实部署成本 |

### 6.2 统计分析模型

| 分析目标 | 模型 | 重点解释 |
|---|---|---|
| 成功率主效果 | `Success ~ ToolCondition × AbilityGroup × TaskFamily + (1 | Participant) + (1 | TaskVariant)` | PSOP 是否显著高于 Manual / Generic AI；PSOP Full 是否高于 Basic；低能力组提升是否最大 |
| 完成时间 | `log(CompletionTime) ~ ToolCondition × AbilityGroup × TaskFamily + (1 | Participant) + (1 | TaskVariant)` | 若 PSOP 时间略长但 false success 大幅降低，不能简单判定低效 |
| False Success | `FalseSuccess ~ ToolCondition + TaskFamily + EvidenceCompleteness` | 预期 PSOP Full 最低 |
| 能力门槛曲线 | `P(Success) = sigmoid(α + β1 AbilityScore + β2 ToolCondition + β3 Interaction)` | 估计 `AbilityThreshold_80`，即达到 80% 成功率所需最低能力分 |

### 6.3 能力门槛曲线的预期表达

| 工具 | 80% 成功所需能力 | 预期含义 |
|---|---:|---|
| Manual | 高 | 传统文档对低能力用户门槛高 |
| Generic AI | 中高 | 自由对话能帮忙，但缺少状态与证据治理 |
| PSOP Basic | 中 | 编译 EG 和 evidence checkpoint 已能降低门槛 |
| PSOP Full | 中低 | Tester + World Model hardening 进一步降低门槛 |

---

## 7. 机制证明与消融设计

### 7.1 真人实验主对比

```text
Manual
  vs Generic AI
  vs PSOP Basic
  vs PSOP Full
```

该对比用于证明整体系统价值：

- PSOP 是否优于静态 SOP。
- PSOP 是否优于自由聊天式 AI。
- PSOP Full 是否优于 PSOP Basic。
- PSOP 是否在物理任务和数字工程任务中都有效。

### 7.2 自动化 ablation

自动化消融不一定进入真人实验，但必须进入 tester。

| Ablation 条件 | 移除内容 | 预期暴露的问题 | 证明机制 |
|---|---|---|---|
| PSOP without evidence requirement | 移除强制证据 | 用户跳步、证据缺失、false success 上升 | evidence checkpoint 的价值 |
| PSOP without active probe | 移除主动补证 | 模糊输入被接受、错误测量未被发现 | active probe 的价值 |
| PSOP without final verify | 移除终局验证 | 未装配 / 未 pass 被判成功 | final verify 的价值 |
| PSOP without state memory | 移除状态记忆 | 重复提问、漏掉前置条件、状态倒退 | Session Token / state management 的价值 |
| PSOP before tester hardening | 使用未强化版本 | guard 漏洞更多 | Tester / World Model 之前的 baseline |
| PSOP after tester hardening | 使用强化版本 | 错误率下降、证据更完整 | 持续改进飞轮 |

### 7.3 机制到指标的映射

| PSOP 机制 | 应影响的指标 | 预期表现 |
|---|---|---|
| PSkill contract | 成功率、适用边界违规率 | 任务边界更清楚，越界更少 |
| Compiler-generated EG | 状态收敛时间、关键节点到达率 | 执行更稳定，少跳步 |
| Evidence checkpoint | 证据完整率、false success | 证据更完整，伪成功更少 |
| Active probe | 测量错误率、manual misunderstanding rate | 关键不确定性更早暴露 |
| Final Verify | false success、终局错误 | 成功判定更严格 |
| Tester / World Model hardening | Basic vs Full 差异 | Full 在错误率、证据完整率和成功率上更优 |

---

## 8. 阶段计划、风险控制与版本冻结

### 8.1 实验阶段计划

| 阶段 | 时间 | 核心产出 | 主要目标 |
|---|---|---|---|
| Phase 0：准备期 | 2-3 周 | 两个 PSkill、两个 compiler-generated EG、两个 validator report、两套 tester suite、两个 scripted world model、3D 打印机与耗材、SHENZHEN I/O 环境、能力测评问卷、专家评分表、安全流程、数据采集表 | 完成实验基础设施 |
| Phase 1：Pilot | 2 周 | 24 人、48 runs、pilot 数据、流程问题清单 | 校准任务难度、打印时间、puzzle 难度、PSOP-EG 稳定性、Generic AI 答案泄漏风险、评分一致性 |
| Phase 2：Tester hardening | 1-2 周 | positive / negative / reverse / counterfactual / regression cases；重编译后的 EG | 修复 PSkill / compiler / validator / world model 漏洞，冻结 Basic / Full revisions |
| Phase 3：主实验 | 4-6 周 | 60 人、120 runs、主数据集 | 输出成功率、完成时间、false success、证据完整率、状态收敛曲线、能力门槛曲线、Basic vs Full、失败分类、用户访谈 |
| Phase 4：扩展验证 | 后续 | 旋钮替代件、传感器支架、线上 3D 打印平台模拟下单、少量真实平台下单、SHENZHEN-like 自建 puzzle、更多 RTFM 环境 | 验证外推性和商业闭环 |

### 8.2 风险控制矩阵

| 风险域 | 主要风险 | 控制措施 |
|---|---|---|
| 3D 打印安全 | 喷嘴烫伤、热床烫伤、用户误用工具 | 使用 PLA；低风险小件；禁止用户接触喷嘴和热床；实验员处理硬件异常；打印机固定位置；防烫提示和消防设备 |
| 3D 打印质量 | 打印失败、材料浪费、设备故障、长时间打印 | 每次实验前校准；首层失败记录但不立即判定用户失败；统一打印机、耗材、profile；所有失败进入 failure taxonomy |
| SHENZHEN I/O 公平性 | AI 直接背答案、用户之前玩过、puzzle 难度不均 | 使用 Coaching Mode；记录 Zachtronics 经验；禁止外部搜索；选择不太常见 puzzle；后续自建 SHENZHEN-like puzzle |
| SHENZHEN I/O 操作门槛 | 用户不会操作界面、截图证据缺失 | 任务前 5 分钟界面训练；要求 pass screenshot；要求用户解释方案 |
| 模型版本 | 模型更新导致结果不可复现 | 固定模型；固定日期窗口；记录模型版本；保存完整对话；统一联网条件 |
| PSOP 版本 | PSkill / EG / tester 变动污染结果 | 锁定 PSkill revision、compiler version、EG revision、tester version、world model version；所有 run 记录版本 |

### 8.3 版本冻结规则

进入真人实验前必须冻结：

1. **PSOP 侧**
   - PSkill revision。
   - Compiler version。
   - EG revision。
   - Validator rule version。
   - Tester suite version。
   - World Model scenario version。
2. **任务侧**
   - 3D 打印任务 variant。
   - OpenSCAD template version。
   - Printer firmware。
   - Slicer version / profile。
   - SHENZHEN I/O puzzle ID / variant。
3. **AI 侧**
   - Generic AI 模型版本。
   - PSOP evaluator 模型版本。
   - 联网 / 不联网策略。

如果 Phase 2 发现问题，修复流程必须是：

```text
修改 PSkill / compiler rule / validator rule / world model scenario spec
  → 重新编译 EG
  → 重新跑 tester
  → 通过后 freeze EG revision
  → 进入真人实验
```

---

## 9. 第一轮交付物、最小可行版本与最终叙事

### 9.1 第一轮交付物

| 类别 | 交付物 |
|---|---|
| PSkill | `PSkill-Desktop3DPrint-FunctionalSparePart`；`PSkill-SHENZHENIO-ManualDrivenPuzzleCompletion` |
| Compiler Artifacts | `Desktop3DPrint-FunctionalSparePart@basic-eg`；`Desktop3DPrint-FunctionalSparePart@full-eg`；`SHENZHENIO-ManualDrivenPuzzleCompletion@basic-eg`；`SHENZHENIO-ManualDrivenPuzzleCompletion@full-eg` |
| Tester | `PrintOps positive / negative / reverse / counterfactual suite`；`SHENZHEN positive / negative / reverse / counterfactual suite` |
| World Model | `PrintOps scripted scenario model`；`SHENZHEN scripted cognitive/error model` |
| 实验数据 | `Participant dataset`；`Run dataset`；`Trace dataset`；`Evidence dataset`；`3D print artifact dataset`；`SHENZHEN artifact dataset`；`Survey dataset`；`Expert scoring dataset` |

### 9.2 最小可行版本

| 项目 | MVP 配置 |
|---|---|
| 人数 | 24 人 pilot |
| 条件 | Manual；Generic AI；PSOP Basic；PSOP Full |
| 任务 | 3D 打印：线缆固定卡扣；SHENZHEN I/O：一个 medium puzzle |
| 打印机 | 2 台同型号 Bambu A1 mini 或 A1；1 台备用同型号机器 |
| 统一变量 | PLA；slicer version；参数化模板；验收夹具；模型版本；PSOP revision |
| 主要成功指标 | 任务成功率；false success；完成时间；证据完整率；状态收敛时间；低能力组提升；PSOP Basic vs Full 差异 |

Pilot 进入主实验的建议门槛：

1. **效果门槛**
   - PSOP Full 对低能力用户有明显提升。
   - PSOP Full 的 false success 明显低于 Generic AI。
2. **流程门槛**
   - PSOP-EG 不频繁卡死。
   - 证据要求不会让用户完全无法推进。
   - final verify 能稳定区分成功、失败和需要补证。
3. **任务门槛**
   - 线缆卡扣任务能在可控时间内完成。
   - SHENZHEN I/O medium puzzle 不会过难或过易。
   - 专家评分一致性可接受。

### 9.3 最终论文叙事

本实验可以形成如下论文叙事：

> 我们提出 PSOP-Bench v0，包含一个真实物理制造任务和一个确定性 RTFM 数字工程任务。第一个任务要求用户使用桌面 3D 打印机完成一个功能性小备件；第二个任务要求用户在 SHENZHEN I/O 中基于手册完成电路 puzzle。两个任务都由 PSkill 描述，并由 compiler 生成 PSOP-EG。Runner 执行 EG，维护 Session Token、证据、状态和 final verification。Tester 与 World Model 生成正向、负向、反向和反事实测试，用于强化 PSkill / compiler / EG。我们比较 Manual、Generic AI、PSOP Basic 和 PSOP Full 在不同能力用户中的表现，评估成功率、完成时间、错误成功率、证据完整率、状态收敛时间和能力门槛曲线。

最终要证明 PSOP 不是普通 agent framework，而是一套：

```text
PSkill source
  → formal execution graph
  → governed runtime
  → evidence-driven execution
  → test-hardened evolution
```

的现实作业执行系统。

### 9.4 最终建议

建议把第一轮实验锁定为：

```text
主实验 1：3D 打印小备件
主实验 2：SHENZHEN I/O 完成 puzzle
```

执行策略：

1. **3D 打印侧**
   - 第一阶段优先选择稳定、低噪声、低维护的新手友好型 FDM 设备。
   - 减少硬件变量，把实验焦点放在 PSOP 的状态管理和证据闭环上。
2. **SHENZHEN I/O 侧**
   - 使用 Coaching Mode。
   - 避免实验变成大模型背答案测试。
   - 重点验证 RTFM、规格抽取、状态追踪和调试闭环。
3. **PSOP 工程侧**
   - 先写 PSkill。
   - 由 compiler 生成 EG。
   - tester 和 world model 发现问题后，修改 PSkill 或 compiler rule。
   - 重新编译 EG。
   - 真人实验只执行冻结后的 EG revision。

---

## 附录 A：原章节到新结构的合并映射

| 原章节 | 新位置 | 合并理由 |
|---|---|---|
| 0-3：核心命题、架构、目标、假设 | 第 1 章 | 合并为实验总览和研究问题矩阵 |
| 4：两个主实验 | 第 1、3、4 章 | 总体对比放第 1 章，细节分别放入两个主实验章节 |
| 5-7：工具条件、能力分组、样本量 | 第 2 章 | 都属于实验设计主框架 |
| 8-12：3D 打印机、任务、PSkill、EG、Tester | 第 3 章 | 全部收束为 3D 打印实验包 |
| 13-16：SHENZHEN I/O 任务、PSkill、EG、Tester | 第 4 章 | 全部收束为 RTFM 数字工程实验包 |
| 17-18：执行流程、数据采集 | 第 5 章 | 执行和数据 schema 需要放在一起 |
| 19-21：指标、分析、机制证明 | 第 6、7 章 | 指标和统计模型独立，机制证明与 ablation 独立 |
| 22-23：阶段计划、风险控制 | 第 8 章 | 都属于实验治理与落地控制 |
| 24-27：交付物、MVP、论文叙事、结论 | 第 9 章 | 收束为第一轮执行包和最终叙事 |

---

## 附录 B：原文参考来源保留

> 以下为原文中已有的外部链接。本版主要重排结构，未重新核验这些外部规格。

[1]: https://www.dayinpai.com/apply/server "3D打印服务在线接单平台 - 打印派"
[2]: https://www.zachtronics.com/shenzhen-io/ "Zachtronics | SHENZHEN I/O"
[3]: https://bambulab.com/en/a1-mini/tech-specs?utm_source=chatgpt.com "Bambu Lab A1 mini - Technical Specifications"
[4]: https://www.prusa3d.com/product/original-prusa-mini-semi-assembled-3d-printer-enclosure-bundle-5/ "Original Prusa MINI+ Semi-assembled 3D Printer - Enclosure Bundle | Original Prusa 3D printers directly from Josef Prusa"
[5]: https://www.creality3dofficial.com/products/ender-3-v3-se-3d-printer "Ender-3V3 SE with Sprite Direct | Ender 3D Printer | 3D Printer for Sale"
[6]: https://bambulab.com/en/download/studio?utm_source=chatgpt.com "Software Bambu Studio"
[7]: https://www.prusa3d.com/p/prusaslicer/ "PrusaSlicer"
[8]: https://openscad.org/ "OpenSCAD - The Programmers Solid 3D CAD Modeller"
[9]: https://www.freecad.org/?utm_source=chatgpt.com "FreeCAD: Your own 3D parametric modeler"
