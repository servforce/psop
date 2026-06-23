# PSOP

PSOP 是一个面向现实物理世界现场作业的 Skill 平台。它的核心思想是：把现实世界中的任务、专家经验、现场证据、安全约束和工具能力构建为可被 AI 重复使用的技能，帮助人类在真实环境中完成复杂作业。

当前仓库聚焦这条核心链路的 MVP 实现：

`Skills -> Publish -> Auto Compile -> Invocation -> Runtime -> Replay / Observability`

## 目录

- [项目简介](#项目简介)
- [主要功能](#主要功能)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [开发命令](#开发命令)
- [测试](#测试)
- [文档](#文档)
- [贡献](#贡献)
- [许可证](#许可证)

## 项目简介

现实现场作业往往依赖纸面 SOP、老师傅经验、临场判断、企业系统和现场证据。PSOP 要解决的问题，是让这些原本分散、不可复用、难以验证的作业能力，沉淀为可以被 AI 调用、被人类执行、被系统观测和持续改进的 Skill。

在 PSOP 中，Skill 不是聊天 prompt，也不是一次输入后自动完成的脚本。Skill 是一个现实世界任务契约：它描述作业目标、适用边界、现场步骤、证据要求、安全约束、异常恢复路径和完成标准。系统将 Skill 编译为正式的 PSOP Execution Graph，并由 Runtime Kernel 引导现场人员逐步完成真实作业。

运行时，一次真实 invocation 会创建 Run、Terminal Session、trace events、terminal events 与 Session Token snapshots。用户反馈、图片、音频、视频、文件、设备确认和现场观察都会作为终端事实进入系统。Replay 与可观测性是默认排障闭环，用于复盘任务执行过程，并为后续 Skill 迭代提供依据。

平台围绕以下核心约束设计：

- `Session Token` 是唯一正式运行时状态对象。
- `Runtime Kernel` 是唯一正式状态主权者。
- `terminal_event` 是终端输入输出的 append-only 事实源。
- 大模型调用必须经过 `LLM Inference Gateway`。
- Replay 只基于已持久化的运行时事实重建。

## 主要功能

- 通过浏览器 Web IDE 构建和管理现实作业 Skill。
- 将 SOP、专家经验、现场步骤、证据要求和安全约束结构化为可发布的 Skill。
- 支持 Git-backed Skill 源码编辑、版本冻结与发布。
- 自动编译生成符合 formal-v5 的 PSOP Execution Graph 产物。
- 通过受控 Gateway 发起真实 terminal invocation，让 Runtime 引导现场人员执行任务。
- 持久化 terminal transcript、runtime trace、Session Token snapshot 与 replay 数据。
- 提供独立 Skill 调试终端，用于模拟真实操作员的现场交互。
- 提供黑盒时序 Skill 测试，验证 Skill 在多模态输入场景下的行为是否符合语义预期。
- 支持面向 OpenTelemetry 的运行时诊断和现场问题复盘。

## 架构概览

```text
backend/      FastAPI 服务端、编译、运行时、任务、Gateway、Repository 与 Agent Harness
static/       基于 Alpine.js 与 Tailwind CSS 的静态 Web IDE
docs/         项目愿景、系统架构、Execution Graph 形式定义与协作规则
tests/        后端、运行时、API、可观测性与对象存储测试
scripts/dev/  根目录开发脚本
```

主要实现模块：

- `backend/app/domain/skills/`：Skill 元数据、源码、发布与 GitLab 集成。
- `backend/app/domain/compiler/`：formal-v5 编译请求、诊断与 EG 产物。
- `backend/app/domain/runtime/`：invocation、run、Session Token snapshot、terminal event 与 replay。
- `backend/app/domain/skill_tests/`：黑盒时序测试场景、资源、运行与 Judge 评估。
- `backend/app/domain/agent_prompts/`：Agent Prompt Pack、版本与 binding。
- `backend/app/agent_harness/`：PSOP 多智能体治理框架的目标模块。
- `static/js/app/`：按页面域拆分的前端交互逻辑。

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 20+
- PostgreSQL
- S3-compatible 对象存储，例如 MinIO
- 用于 Skill 源码集成的 GitLab token
- OpenAI-compatible 大模型接口

### 克隆仓库

```bash
git clone https://github.com/servforce/psop.git
cd psop
```

### 准备配置

```bash
cp .env.example .env
```

根据本地环境编辑 `.env`，配置数据库、GitLab、对象存储与大模型接口。

### 安装后端依赖

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cd ..
```

### 安装前端依赖

```bash
cd static
npm ci
npm run build:css
cd ..
```

### 启动本地联调

```bash
scripts/dev/start.sh
```

后台启动并把日志写入项目根目录 `logs/`：

```bash
scripts/dev/start-background.sh
```

默认情况下，后端运行在 `http://127.0.0.1:8001`，Web IDE 运行在 `http://127.0.0.1:4173`。

## 配置说明

根目录 `.env.example` 描述了本地联调所需的主要配置。最重要的配置分组包括：

- `PSOP_DATABASE_*` 或 `PSOP_DATABASE_URL`
- `PSOP_GITLAB_*`
- `PSOP_OBJECT_STORE_*`
- `PSOP_RAW_MATERIAL_*`
- `PSOP_VIDEO_*`
- `PSOP_LLM_*`
- `PSOP_ASR_*`
- `PSOP_OTEL_*`
- `PSOP_SERVER_*`
- `PSOP_WEB_*`

开发脚本会读取根目录 `.env` 与 `backend/.env`，并为缺失的 host、port 等本地联调参数补齐默认值。

LLM Inference Gateway 只暴露两类能力路由：`text` 与 `multimodal`。旧的 `PSOP_LLM_DEFAULT_MODEL`、`PSOP_LLM_SKILL_CREATION_*`、`PSOP_LLM_VISION_MODEL` 已废弃；请改用 `PSOP_LLM_TEXT_*` 与 `PSOP_LLM_MULTIMODAL_*`。

## 开发命令

常用根目录脚本：

```bash
scripts/dev/run-server.sh
scripts/dev/run-web.sh
scripts/dev/build-web.sh
scripts/dev/test-server.sh
scripts/dev/test-web.sh
```

也可以直接运行：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
cd static && npm test -- --runInBand
cd static && npm run build:css
```

## 测试

运行后端与前端测试：

```bash
scripts/dev/test-server.sh
scripts/dev/test-web.sh
```

提交前如果修改了前端样式，请重新生成编译后的 CSS：

```bash
scripts/dev/build-web.sh
```

## 文档

项目主文档按以下顺序阅读：

- [项目愿景](docs/overview/vision.md)
- [系统架构设计](docs/architecture/system-architecture.md)
- [Execution Graph 形式定义](docs/architecture/execution-graph-formal-v5.md)
- [Agent 协作规则](docs/engineering/agent-rules.md)

参考资料位于 [docs/reference](docs/reference/)。

## 贡献

当前仓库主要通过 feature branch 开发。实现变更需要与 `docs/overview/vision.md`、`docs/architecture/system-architecture.md` 和 `docs/architecture/execution-graph-formal-v5.md` 保持一致；如果行为发生变化，应先更新或同步更新对应设计文档，再推进代码实现。

本地验证优先使用：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q
cd static && npm test -- --runInBand
cd static && npm run build:css
```

## 许可证

当前仓库尚未添加 license 文件。在维护者补充明确许可证前，请按私有/专有项目处理。
