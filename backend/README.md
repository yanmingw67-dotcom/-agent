# 产品功能参数评估系统 · P1.3

本项目依据上级目录的开发设计书，实现 **安全导入、静态索引、共享只读检索及候选知识调查**。

P1.2 完整运行与基准说明见 `docs/P1.2_RUNBOOK.md`，架构边界见 `docs/P1.2_ARCHITECTURE.md`。
初始验收见 `P1.2_ACCEPTANCE_REPORT.md`；第一轮整改见 `P1.2_REMEDIATION_REPORT.md`。
最新迁移为0005；原 P1.1、P1.2 测试、迁移与历史日志保留。
P1.3新增Pydantic AI Knowledge Builder、真实来源证据登记、候选能力与假设关系，仍不含发布或Agent B。
运行与模型准入见 `docs/P1.3_RUNBOOK.md`，架构见 `docs/P1.3_ARCHITECTURE.md`；验收见 `P1.3_ACCEPTANCE_REPORT.md`。
导入成功仅表示 `stage=extracted`，不是知识库就绪。

## 快速启动（Docker Compose）

在本目录执行，要求 Docker Engine、Compose 和 Python 3.12+；推荐使用 uv。
容器基于官方 ghcr.io/astral-sh/uv 的固定版本 Python 3.12 镜像，不要求连接 Docker Hub 获取 Python。

```sh
uv sync --frozen
uv run python scripts/generate_env.py
docker compose up --build -d --wait --wait-timeout 180
docker compose ps
```

若 .env 已存在，生成脚本会拒绝覆盖；直接使用原配置，不要重置数据库密码。
生成的 .env 含本地随机凭据，权限 0600，已经被 Git 和构建上下文排除。
不要将 .env、storage 或 Docker 数据卷交给审查方。

API 监听本机 8000 端口；OpenAPI 文档路径为 `/docs`，健康接口为
`/health/live`、`/health/ready`。后者验证数据库表及 Redis 可达。
首次 api 启动先运行 Alembic；worker 等待 api 就绪。
端口占用时可用 `API_PORT=18000 docker compose up -d --wait`。
Compose 中数据库和 Redis 不对主机暴露端口。
停止服务用 `docker compose stop`，保留数据；不要使用带 -v 的清理命令删除审查证据。

## 测试

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python scripts/generate_fixtures.py
uv run python scripts/compose_smoke.py
docker compose exec -T api alembic current
docker compose exec -T api alembic check
```

pytest 不需要模型 Key，也不需要启动 PostgreSQL/Redis。
API 集成测试使用真实 ASGI/上传/文件存储/解压与 SQLite 测试数据库，
队列故障与 outbox 投递测试使用明确命名的 Queue 测试替身。
它们不证明 PostgreSQL 锁、触发器或 RQ 实际运行；应补充以下真实基础设施验证。

```sh
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --wait postgres redis
uv run python -m scripts.infrastructure_check
```

此模式仅将 PostgreSQL/Redis 端口映射到本机 15432/16379；API 和 RQ SimpleWorker 使用
本机 Python 3.12，真实迁移和队列执行不使用 Mock，但不替代四服务容器验收。
结果写入 reports/infrastructure-check.json。数据库行锁并发压力测试尚未专项执行。
compose_smoke 不使用队列 Mock，创建人工测试品类并验证真实后台任务，结果写入
`reports/compose-smoke.json`；重跑会新增人工测试记录，不删除已有数据。
所有测试源码都是人工公开样例，没有导入任何企业真实源码。
实际执行记录见 `reports/` 和 `P1.1_ACCEPTANCE_REPORT.md`。

## 接口范围

|方法|路径|权限与行为|
|---|---|---|
|POST|/api/v1/categories|admin，建立品类，冲突 409|
|GET|/api/v1/categories|admin/read，只返回品类与知识就绪标记|
|POST|/api/v1/categories/{id}/source|admin，multipart 字段 file，202 返回 snapshot_id/job_id|
|GET|/api/v1/jobs/{id}|admin，状态、阶段、错误、耗时及统计|
|POST|/api/v1/jobs/{id}/retry|admin，仅失败任务，202；冲突 409|
|GET|/health/live、/health/ready|存活/依赖就绪检查|

鉴权格式为 `Authorization: Bearer <ADMIN_TOKEN 或 READ_TOKEN>`。
Token 从私有 .env 读取，不要写入命令历史或公开截图。
源码名必须为安全的 .zip 文件名，MIME 支持 application/zip、
application/x-zip-compressed 和 application/octet-stream，另检查内容签名。
传输签名不合法返回 422，文件/请求超限返回 413，权限不足返回 403；
深度 ZIP 结构/安全问题由异步任务返回 failed 和稳定 error_code。
不提供源码下载静态链接。

真实可复现样例可由 `uv run python -m scripts.infrastructure_check` 执行；
四服务部署成功后另运行 `scripts/compose_smoke.py` 验证容器链路：
人工 demo_waf_source.zip → 上传 202 → 后台校验与解压 → 任务 extracted，
产生 4 个保留文件、2 个排除文件和逐文件 SHA-256 manifest。
manifest不是证据记录。需先索引，再由管理员显式调用investigate启动模型调查；
只有完整回读并经服务端核验后才生成evidence_id，能力仍仅为待人工核验候选。

## 本地开发（不用容器运行 API）

需要自行提供可达 PostgreSQL、Redis，并配置 .env 的 DATABASE_URL/REDIS_URL。
开发测试只需 uv sync 和 pytest，无需本地服务。

```sh
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
# 在另一终端执行
uv run python -m app.jobs.worker
```

数据库迁移文件在 app/db/migrations；迁移只创建本批表，之后按批次追加。
生产环境建议部署前单独执行迁移；当前 Compose 的启动迁移适合本批单 API 实例。

## 配置与恢复

资源限额见 .env.example；如需覆盖 Compose 内限额，在服务 environment 中显式添加。
ADMIN_TOKEN、READ_TOKEN 必须不同且各至少 32 字符，无固定演示口令。
任务在 Redis 故障期间保持 queued，Worker 恢复后扫描数据库继续投递。
失联任务在执行超时加 60 秒后标 failed；管理员检查后调用 retry。
非法归档的重试仍失败；修正源码后应提交新的 ZIP。
归档、manifest 和解压目录均按后台 UUID 存储；不会将上传文件名当作服务器路径。

## P1.1 代码审查整改复验

```sh
uv run pytest -q
REPORT_DIR=reports/remediation uv run python scripts/compose_smoke.py
REPORT_DIR=reports/remediation uv run python -m scripts.compose_recovery_check
```

重启恢复脚本会暂时停止本项目 Worker、重启数据库/Redis及四服务，仅在本项目独立验收环境运行；
不会删除数据。它验证 Worker 停机接收任务、队列与数据库重启后消费，以及完成任务/manifest 持久化。
不要在共享生产环境直接运行。

真实数据库/队列竞态专项使用本机端口 15432/16379，须先启动 local override，且避免与常驻 Worker
同时扫描同一数据库。在独立验收环境执行：
`uv run python -m scripts.remediation_infrastructure_check`。
此脚本新日志位于 reports/remediation，不覆盖初始报告。

新增迁移 0002 记录 Redis 投递确认；先提交数据库 token 与租约，再投递 Redis，
同一任务尝试的消息 ID 稳定，重试后才更换。详见 docs/ARCHITECTURE.md。
二进制分类检查完整流，带 BOM UTF-16/UTF-32 保留为待解析编码，不作为已完成文本索引。

存储配额、保留/清理和未覆盖故障矩阵见 docs/P1.1_STORAGE_AND_RECOVERY_PLAN.md。
本批不新增自动删除入口。

## 文档与停止点

- docs/IMPLEMENTATION_PLAN.md：五批次与本批详细计划。
- docs/ARCHITECTURE.md：模块、数据、一致性、权限与安全取舍。
- CHANGELOG_P1.1.md：变更文件和未实现项。
- P1.1_ACCEPTANCE_REPORT.md：实际测试、质量门槛、限制与下一批建议。

P1.3交付后暂停，等待代码审查及验收意见；P1.4～P1.5未实施，不自动推送本轮提交。
以下 P1.1 整改脚本仍用于历史回归，不替代本批 P1.2 基准。
