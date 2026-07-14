# PSOP Backend

该目录包含 PSOP 的 FastAPI API、SQLAlchemy 领域模型、Runtime Kernel、
Agent Harness、对象存储适配器与 PostgreSQL `runtime_job` worker。

## 本地开发

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

从仓库根目录分别启动 API 与 worker：

```bash
scripts/dev/run-server.sh
scripts/dev/run-worker.sh
```

也可用 `scripts/dev/start.sh` 同时启动 API、worker 和静态 Web。API 默认设置
`PSOP_RUNTIME_WORKER_EMBEDDED_ENABLED=false`；embedded 模式仅用于测试或紧急回滚。

worker 将任务隔离到三个 pool：`runtime-interactive` 只处理 `runtime`，
`build-test` 处理编译与时间线驱动，`material` 处理素材分析与生成。生产使用
PostgreSQL `LISTEN/NOTIFY` 把 worker 的 terminal/trace 提示送到 API，REST 与
`from_seq` 增量读取仍是恢复的权威路径。

## 测试

```bash
scripts/dev/test-server.sh
```
