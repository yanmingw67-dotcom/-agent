# CHANGELOG · P1.1

## 实现

- Python 3.12+ FastAPI/Pydantic v2 工程与可复现 uv 依赖锁。
- 产品品类创建/查询，后台管理员与只读鉴权，无固定演示密钥。
- 分块上传、内容签名与限额、服务端 UUID 归档、SHA-256。
- ZIP 中央目录预检、路径规范化、链接/特殊文件/碰撞与炸弹防护、
  实际展开量校验、CRC、敏感标记、排除报告、原子目录提交。
- PostgreSQL/Alembic 品类、快照、任务、审计；生效指针与不可变哈希保护。
- 数据库 outbox + Redis/RQ，阶段和执行状态、重复领取保护、显式失败重试、
  失联恢复、错误码、耗时及模型零调用统计。
- 四服务 Compose、开发基础设施 override、非 root、资源限制与只读源码视图。
- 57 项 pytest 测试；实际 PostgreSQL/Redis/RQ 集成脚本；人工公开 ZIP。
- 运行手册、架构、安全边界、五批次计划与有条件验收报告。

## 真实验证

pytest 57 passed；ruff 与格式检查通过；真实 PostgreSQL 迁移及 Alembic 一致性检查通过；
真实 Redis/RQ 完成合法导入、恶意包拒绝及失败重试。
完整 Compose 构建因 Python 镜像下载失败受阻，未声明通过；详见验收报告及 reports 原始日志。

## 未实现与停止点

NOT_IMPLEMENTED：静态源码数据库索引、AST/符号、文档处理、共享只读检索工具、
Agent A、证据 ID、能力画像、关系/等效候选、审核发布、Probe、完整 Agent B。
Pydantic AI、Tree-sitter、Embedding 不在本批引入；在对应批次锁定版本并验证。
本批不提供假能力和假证据。P1.1 交付后停止新增功能。

## 变更文件清单

除原始附件之外均为本次新建；运行时 .env/.venv/storage 不属于交付源码。
完整逐文件清单见 FILE_MANIFEST_P1.1.txt。

- 项目根：.gitignore。
- backend：pyproject.toml、uv.lock、Dockerfile、.dockerignore、.env.example、
  docker-compose.yml、docker-compose.local.yml、alembic.ini、README.md。
- backend/app/api：categories.py、knowledge_uploads.py、jobs.py、schemas.py、__init__.py。
- backend/app/core：auth.py、config.py、security.py、logging.py、__init__.py。
- backend/app/ingestion：archive_guard.py、content_store.py、__init__.py。
- backend/app/jobs：worker.py、handlers.py、queue.py、status_machine.py、__init__.py。
- backend/app/db：models.py、session.py、__init__.py、migrations/env.py、
  migrations/versions/0001_ingestion.py。
- backend/app：main.py、__init__.py；agents/knowledge/retrieval 的边界占位 __init__.py。
- backend/tests：conftest.py、__init__.py；unit 安全 ZIP、元数据、请求守卫、状态机；
  integration/test_import_api.py；fixtures/demo_waf_source.zip。
- backend/scripts：generate_env.py、generate_fixtures.py、compose_smoke.py、infrastructure_check.py。
- backend/docs：IMPLEMENTATION_PLAN.md、ARCHITECTURE.md。
- backend/reports：各轮 pytest/ruff、迁移 SQL、部署失败日志、真实基础设施检查日志与 JSON。
- backend：本 CHANGELOG、P1.1_ACCEPTANCE_REPORT.md、FILE_MANIFEST_P1.1.txt。
