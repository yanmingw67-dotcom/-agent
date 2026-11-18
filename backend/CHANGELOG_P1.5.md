# P1.5 变更说明：只读 Knowledge Probe

基线：`f2a43329ef353ebef13747ca0ea0a8d5219bae72`（P1.4 验收基线）。
本轮只实现 P1.5 只读 Probe，不进入 Agent B，不新增模型调用、自动审核或发布写入。

## 本轮变更

- 新增 `POST /api/v1/probes`：输入仅 `category_id`、单条 `requirement_text`
  与有界 `limit`；额外字段、查询参数、空白输入和越界均严格拒绝。
- 新增 `app/retrieval/probe.py`、`probe_schemas.py`、`app/api/probe.py`：
  每请求独立 PostgreSQL `REPEATABLE READ READ ONLY` 事务，绑定品类当前生效发布/快照；
  不接受调用方指定版本、快照或发布。
- 发布一致性：校验品类双指针、发布记录与快照的品类/快照/发布状态、清单作用域与
  `payload_hash`；不一致时 fail closed。
- 修订新鲜度：只读取冻结发布清单中的已审核修订；旧批准修订若已有更新审核，
  保守隐藏并返回 `NEWER_REVIEW_NOT_PUBLISHED`，不自动采用未发布修订。
- 证据回读：按同一快照复验来源哈希、引文哈希、文档哈希和证据身份；失效证据不进入
  结果，只返回结构化缺口。
- 检索为确定性词法匹配：不调用模型、不生成满足/不满足或合规结论；
  `formal_compliance_decision` 恒为 `null`，`support_status` 恒为 `not_assessed`。
- 输出有界：清单最多 100 个能力/4 MiB，证据最多 100 条，完整响应最多 128 KiB；
  超限返回 `truncated`/413，不静默裁掉能力边界；响应 `Cache-Control: no-store`。
- 服务拆分：仅 `SERVICE_MODE=retrieval` 执行 Probe；后台 API 返回
  `503 PROBE_RETRIEVAL_SERVICE_REQUIRED`；检索服务不注册审核/上传/发布/回滚路由。
- `docker-compose.yml` 为 retrieval 增加 `documents:/data/documents:ro`，
  使文档证据可在只读挂载下复验；未改变原有资源配额或服务数量。
- 新增 P1.5 测试：Probe 主流程、边界、失败/证据失效、端到端、真实 PostgreSQL
  一致性（含发布切换与只读事务）。
- 新增验收/基准脚本：`p15_acceptance.py`、`p15_benchmark.py`、
  `p15_builder_load.py`、`p15_agent_load.py`、`p15_builder_functional_load.py`。
- 新增文档：`docs/P1.5_ARCHITECTURE.md`、`docs/P1.5_RUNBOOK.md`、
  `P1.5_ACCEPTANCE_REPORT.md` 与本变更说明。

## 兼容性

- 不修改 P1.1～P1.4 的业务代码、迁移、测试或历史验收记录；
  仅新增 P1.5 路由、检索实现、测试与文档，并在 `app/main.py` 注册 Probe 路由。
- 不新增数据库迁移；复用既有发布/修订/证据模型。
- 不改变现有 Agent A 模型准入、Builder 资源配额、Writer/Indexer 队列或认证方式。
- Probe 是只读接口；管理写接口、发布与回滚行为保持原语义。

## 验证结果

| 项目 | 结果 | 证据 |
|---|---|---|
| 全量 pytest | 230 passed，26 skipped | `reports/p15/pytest-full-final.log` |
| Ruff lint/format | 全部通过；131 files formatted | `reports/p15/ruff-final.log`、`ruff-format-final.log` |
| 真实 PostgreSQL | 18 tests OK | `reports/p15/postgres-p15-final.log` |
| Compose 端到端 | 44/44 checks，25 steps | `reports/p15/p15-acceptance-compose.json` |
| 性能基线 | 0 错误；负载 4 并发 P95 43.652 ms | `reports/p15/p15-probe-benchmark-compose.json` |
| Agent A 运行时 | 160/160 成功；P95 49.421 ms | `reports/p15/p15-probe-agent-load-compose.json` |

本轮真实模型请求 0、费用 0 USD；真实模型链复用 P1.3/P1.4 记录。
Agent 负载使用 FunctionModel 驱动真实运行时，标记 `TEST_FUNCTION_MODEL`，
不是真实模型能力验收。

## 边界与未完成

- 性能数据来自 Docker Compose cgroup 配额，不是独立 4C8G 虚拟机实测。
- 未重跑真实模型；FunctionModel 是测试替身，不代表模型能力、延迟或 Token 成本。
- 公开 12 文件人工样例不代表企业规模；未做长稳、容量规划或生产并发承诺。
- 标准 pytest 跳过真实 PostgreSQL 项；一致性结论以 Compose 内 18 项真实 PG 测试为准。
- 未进入 Agent B、自动能力合并、自动审核/发布或语义检索。
- 未提交任何 API Key、密码、`.env` 或企业源码。

## 交付

- 验收范围与边界：`P1.5_ACCEPTANCE_REPORT.md`
- 架构/降级/安全边界：`docs/P1.5_ARCHITECTURE.md`
- 运行与复现：`docs/P1.5_RUNBOOK.md`
- 原始日志与 JSON：`reports/p15/`
