# PSOP

本仓库当前仅维护 `PSOP Web IDE` 与 `PSOP Server` 两部分正式工程代码。

## 目录结构

```text
backend/      服务端代码
static/       Web IDE 前端静态工程
docs/         架构与详细设计文档
tests/        服务端与集成测试
scripts/dev/  根级开发脚本
```

## 开发入口

推荐优先使用根级脚本：

```bash
scripts/dev/build-web.sh
scripts/dev/test-web.sh
scripts/dev/test-server.sh
scripts/dev/run-web.sh
scripts/dev/run-server.sh
scripts/dev/start.sh
```

## 说明

- `backend/` 使用独立 Python 虚拟环境 `backend/.venv`
- `static/` 使用本地 Node.js 依赖

## 本地联调

推荐先准备根目录 `.env`：

```bash
cp .env.example .env
```

联调时最关键的变量有：

- `PSOP_DATABASE_*` 或 `PSOP_DATABASE_URL`
- `PSOP_GITLAB_TOKEN`
- `PSOP_GITLAB_SKILLS_GROUP_PATH`
- `PSOP_SERVER_HOST` / `PSOP_SERVER_PORT`
- `PSOP_WEB_HOST` / `PSOP_WEB_PORT`
- `PSOP_WEB_API_BASE_URL`

开发脚本会自动读取根目录 `.env` 与 `backend/.env`，并为本地联调补齐默认值。

常用方式：

```bash
scripts/dev/start.sh
```

这会同时启动后端和前端，并让前端自动指向 `PSOP_WEB_API_BASE_URL`。
