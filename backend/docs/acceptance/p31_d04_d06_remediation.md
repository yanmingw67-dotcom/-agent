# P3.1 D04/D06 整改与管理员入口核查说明

| 项目 | 内容 |
| --- | --- |
| 验收编号 | `P31-20261119-1340` |
| 整改轮次 | 2026-11-19 补充整改（仅 D04、D06 与管理员入口核查） |
| 整改起点 | `284ef175f74e9dfe6d99b9773d78299e215b7e1b`（本轮开始时 HEAD） |
| 整改代码提交 | `f953f10564dc0026757103c26b607ca52b27c62b`；报告与日志在后续文档提交中引用 |
| 真实输入 | `1-WAF招标技术参数表.docx`，SHA-256 `94052a70677167d92a2ec50b8933024450283f7e65d5dfc652f602b57b32af3c`，43817 B |
| 结论 | `P31-D04` 已修复；`P31-D06` 已修复；管理员入口核查为 `ADMIN_API_ONLY` |
| 总体状态 | 仍为 **BLOCKED**；B/C/D1/D5–D10/E4b 等未验收项不变 |

> 本轮只处理 D04、D06，并核查 Agent A 管理员上传入口。没有开发新的管理页面，没有导入真实产品源码或正式技术文档，没有调用外部模型，没有修改共享生产环境、正式库或历史验收证据。

## 一、P31-D04：同一原子要求中的多个空白阈值

### 1.1 根因

修复前，阈值识别只返回一条正则命中。对于：

```text
软件版单实例资源占用不高于 ______vCPU / ______GB 内存（按规格填写）
```

第一个空白值有 `不高于` 运算符，可以被识别；第二个 `______GB 内存` 前面没有运算符，不会被识别为独立约束。结果是原文完整保留，但结构化原子要求只覆盖第一个值，第二个值只能落在备注或原文里，不能独立参与顶层逻辑聚合。

### 1.2 修复方式

- 新增“无运算符空白值 + 单位”的识别，单位集合与既有阈值一致。
- 在一条原子要求内枚举全部独立量化约束：有运算符的阈值和无运算符的空白阈值都计入，重叠命中只保留一次。
- 只有两个量化约束之间存在明确分隔符（`/`、`、`、`，`、`；`、`且`、`并且`、`同时`、`以及`）时才拆分；无法安全拆分时保持原样，不猜测。
- 拆分后的片段按 AND 子叶处理，并继承最近的前置运算符。例如第二个 `______GB` 继承 `不高于`，阈值为空，单位为 `GB`。
- 每个片段的 `source_span` 直接取自原文连续区间，仍满足 `parameter.original_text[start:end] == requirement.original_text`。
- 修正“至少/不少于 N”在“一个指标两个边界”场景中的误判：只有 `N 种/项/路` 或前面出现“支持/具备/提供/包含/覆盖”时，才按最小数量处理；否则允许“不少于 10000 个且不超过 20000 个”拆成两个边界。
- 未新增 Schema、数据库列或迁移；API 只增加可选的失败说明字段，见 D06。

### 1.3 D04 不变量核对

| 不变量 | 结果 |
| --- | --- |
| 片段逐字回指原文，不改写、不跨句拼接 | PASS |
| 不新增原文不存在的指标或单位 | PASS |
| 不改变既有 AND/OR/AT_LEAST 语义 | PASS |
| 全部必要量化条件参与顶层逻辑聚合 | PASS；拆分片段进入同一参数逻辑树 |
| 人工编辑后重新解析并保留审计 | PASS；见 `test_p21_multi_threshold_edit.py` |
| 既有单阈值数据兼容 | PASS；单阈值路径不变，历史记录不迁移 |
| 人工修改后重建 | PASS；编辑接口重新解析并写修正审计 |
| 新增字段/迁移 | 无 Schema/迁移变更；不需要升级脚本或回退脚本 |
| 历史评估记录 | 不重写历史 job 的参数和覆盖 JSON；旧任务仍保留 199 原子 |

### 1.4 D04 测试覆盖（至少 10 类）

| 类别 | 用例结果 |
| --- | --- |
| CPU + 内存两个空白阈值 | PASS；单位分别为 `vCPU`、`GB` |
| 三个空白阈值 | PASS；单位分别保留 |
| 确定值 + 空白值混排 | PASS；`10Gbps` 与 `______ms` 同时存在 |
| 同单位不同指标 | PASS；误报率、漏报率均为 `%`，不合并 |
| 不同单位不同指标 | PASS；`Gbps` 与 `万` 独立 |
| 一个指标两个边界 | PASS；`不少于 10000 个` 与 `不超过 20000 个` 同时保留 |
| 括号内空白阈值 | PASS；括号内两个空白值均可识别 |
| AND/OR 嵌套 | PASS；逻辑树同时包含 OR 与 AND，量化叶子不丢失 |
| 人工修改后重建 | PASS；编辑后重新解析，审计可追溯 |
| P2.2 多空白安全降级 | PASS；空白叶子输出“无法确认”，不会因另一个叶子“支持”而整体判支持 |

测试文件：

- `tests/unit/test_p21_multi_thresholds.py`（10 个用例）
- `tests/integration/test_p21_multi_threshold_edit.py`（人工修改后重建与审计）

## 二、P31-D06：失败任务与覆盖状态不一致

### 2.1 根因

修复前，覆盖完整性只检查解析缺口和歧义，没有把“是否真的生成了有效顶层参数”纳入判断。一个任务可能：

- `status=failed`；
- `coverage_status=complete`；
- `extracted_count=0`。

前端只读取状态文字，消费者也只检查 `coverage_status` 时，会把“没有可评估参数”的失败任务误认为可以进入 P2.2。

### 2.2 修复方式

- 提取成功条件改为：至少有一个候选参数、没有解析缺口、没有歧义区域。
- 无有效顶层参数时写入稳定失败码 `NO_TECHNICAL_PARAMETERS`，并在覆盖 JSON 中写入具体 `failure_reason`，包含过滤区域数、过滤原因计数、歧义计数和解析缺口数。
- 结果写回按三类处理：
  - `succeeded`：有候选且覆盖完整；
  - `partial`：有候选但覆盖不完整；
  - `failed`：没有任何候选，覆盖状态强制为 `incomplete`。
- 新增统一的“有效任务状态”计算：对历史脏数据做只读归一化。
  - `failed + complete` 的响应归一化为 `failed + incomplete`；
  - 写入 `state_warnings=["FAILED_JOB_COVERAGE_COMPLETE"]`；
  - 原始 `coverage_status` 保留在 `coverage.original_coverage_status`，不篡改数据库历史。
- `job_json()` 增加 `error_message`、`state_warnings`，并让 `review_required` 覆盖失败任务。
- 失败任务重试时重置覆盖状态为 `incomplete`，记录 `retry_pending` 与 `previous_attempt`，避免旧 complete 被继续展示。
- P2.2 创建评估增加门禁：失败任务返回 `EVALUATION_EXTRACTION_FAILED`；提取数为 0 返回 `EVALUATION_NO_VALID_PARAMETERS`。
- 工作台结果摘要使用归一化状态，并新增 `EXTRACTION_FAILED` 警告。
- 前端提取详情页：
  - 失败时显示 `error_message`（或覆盖中的 `failure_reason`）和错误码；
  - 复选框与“确认提取并启动评估”按钮统一使用 `canEvaluate`；
  - `canEvaluate = 非处理中 && status !== "failed" && parameters.length > 0`。

### 2.3 D06 测试覆盖（至少 10 类）

| 类别 | 用例结果 |
| --- | --- |
| 解析异常 | PASS；`FILE_NOT_FOUND`，failed + incomplete，含具体原因 |
| 无技术参数 | PASS；`NO_TECHNICAL_PARAMETERS`，failed + incomplete |
| 全部区域被过滤 | PASS；不显示 complete |
| 部分有效、部分无法解析 | PASS；`partial` + incomplete |
| 完整成功 | PASS；`succeeded` + complete，计数一致 |
| 异步未处理异常 | PASS；`TENDER_INTERNAL_ERROR`，failed + incomplete |
| 失败任务重试 | PASS；重置 complete，重试后可成功 |
| 前端失败展示 | PASS；显示失败原因、错误码，空参数列表禁用评估 |
| 禁止失败任务进入 P2.2 | PASS；返回 `EVALUATION_EXTRACTION_FAILED` |
| 历史失败任务兼容 | PASS；旧 job 返回 failed + incomplete + 警告，不崩溃 |

测试文件：`tests/integration/test_p21_status_consistency.py`（10 个用例）。

### 2.4 历史数据兼容实测

| 任务 | 处理前 | 当前响应 |
| --- | --- | --- |
| `88ea10d9-1e4f-49b6-8e23-7b32dc2cedaa` | failed，coverage 曾为 complete，0 参数 | failed、incomplete、`FAILED_JOB_COVERAGE_COMPLETE`、`NO_TECHNICAL_PARAMETERS`；0 参数 |
| `e8af956e-3982-4072-adb2-319f7da9f82c` | 修复前成功任务，199 原子 | succeeded、complete、无警告；15 参数、199 原子（历史数据不重写） |

历史任务可以安全展示，前端不会因 0 参数或旧 complete 标记崩溃。旧的 199 原子记录没有迁移；重新提取才会生成 200 原子。

## 三、真实文件复测

本轮不是用合成文件替代，而是重新上传原始真实 Word 文件。

| 项目 | 实测 |
| --- | --- |
| 文件 | `.monkeyai/uploads/attachment-3792846006/1-WAF招标技术参数表.docx` |
| SHA-256 | `94052a70677167d92a2ec50b8933024450283f7e65d5dfc652f602b57b32af3c` |
| 大小 | 43817 B |
| 上传接口 | `POST /api/v1/tender/extractions/upload` |
| 任务 | `60d10838-416e-46fd-92ae-0e010d96afca` |
| 幂等键 | `p31-real-20261119-07` |
| 最终状态 | `succeeded` |
| 覆盖状态 | `complete`，`coverage_incomplete=false` |
| 错误码 / 消息 | 无 |
| 状态警告 | 空 |
| 顶层参数 | 15 |
| 编号子条款 | 46（第 1 项 4 个，其余 3 个） |
| 原子要求 | 200（修复前同一文件为 199） |
| 顶层子节点 | `4,3,3,3,3,3,3,3,3,3,3,3,3,3,3` |
| 区间错误 | 0 |
| 逻辑树与叶子不一致 | 0 |
| 原文定位一致性 | PASS |
| 耗时 | 约 49 ms |
| 模型调用 | 0 |
| 复核标记 | `review_required=true`；第 2 项“至少包括”仍为开放枚举 |

### 3.1 空白阈值明细

`blank_threshold` 总数 9，其中带单位的量化空白阈值 8 个：

| 参数 | 指标 | 原文片段 | 单位 | 运算符 |
| --- | --- | --- | --- | --- |
| 第 1 项 | 采购形态 | `本次采购形态为：______（按项目填写）` | 无 | 无 |
| 第 10 项 | 网络层吞吐 | `网络层吞吐不低于 ______Gbps` | Gbps | 不低于 |
| 第 10 项 | 应用层吞吐 | `应用层吞吐不低于 ______Gbps` | Gbps | 不低于 |
| 第 10 项 | 并发连接数 | `并发连接数不低于 ______万` | 万 | 不低于 |
| 第 10 项 | 新建连接数 | `新建连接数不低于 ______万/秒` | 万 | 不低于 |
| 第 14 项 | 误报率 | `误报率不高于 ______%（建议不超过 1%）` | % | 不高于 |
| 第 14 项 | 漏报率 | `漏报率不高于 ______%` | % | 不高于 |
| 第 15 项 | CPU 占用 | `软件版单实例资源占用不高于 ______vCPU` | vCPU | 不高于 |
| 第 15 项 | 内存占用 | `______GB 内存（按规格填写）` | GB | 不高于（继承） |

第 1 项是无单位填空，不是量化阈值；它不计入 8 个带单位的空白阈值，仍保留给人工填写。第 14 项括号内的“建议不超过 1%”仍属于 `P31-D05`，本轮没有把它伪造成强制主阈值。

### 3.2 199 到 200 的变化原因

第 15 项原有两个空白约束：`不高于 ______vCPU` 和 `______GB 内存`。修复前只把第一个 `vCPU` 约束结构化为原子要求，第二个 `GB` 约束留在原文/备注中。修复后第二个约束成为独立 AND 叶子，因此原子要求从 199 增加到 200。其余参数、编号子条款和逻辑关系数量未变。

### 3.3 覆盖原因

```text
document_title=1
non_technical_section=9
proof_material=3
proof_or_commercial_column=15
section_heading=4
table_header=1
extracted=15
skipped=33
ambiguous=0
parse_gaps=0
```

## 四、回归与静态检查

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| D04/D06 定向测试 | 20 passed | `p31_d04_d06_after_fix.log` |
| P2.1–P2.3 定向回归 | 165 passed | `p31_d04_d06_regression.log` |
| 全量 pytest | 444 passed, 32 skipped | `p31_d04_d06_full_pytest.log` |
| PostgreSQL 专项（P2.1 + P2.2） | 2 passed | `p31_d04_d06_postgres.log` |
| Ruff check | 通过 | 本轮命令输出 |
| Ruff format | 203 files already formatted | 本轮命令输出 |
| 前端类型检查 / 构建 | 均通过 | `p31_d04_d06_frontend.log` |
| 敏感信息扫描 | 未发现本轮新增 Token/密码/API Key | `p31_d04_d06_secret_scan.log` |

全量 pytest 在本轮代码修复后执行；随后只做了 Ruff 要求的 `strict=False` 显式化与未使用 import 清理，并重新执行了 20 个定向用例和 165 个 P2.1–P2.3 回归用例。

## 五、Agent A 管理员上传入口核查

### 5.1 结论

```text
ADMIN_API_ONLY
```

没有可交付的管理员前端页面；管理员能力只通过后端 API 提供。不得把普通用户的 P2.3 页面或普通招标文件上传入口描述为管理员入口。

### 5.2 实际存在的管理员入口

| 能力 | 路径 | 权限 | 实测 |
| --- | --- | --- | --- |
| 品类建立 | `POST /api/v1/categories` | admin | API 存在 |
| ZIP 源码上传 | `POST /api/v1/categories/{category_id}/source` | admin | API 存在；multipart 字段 `file`；仅 `.zip` |
| 能力草案 | `GET /api/v1/admin/capabilities/{claim_id}` | admin | API 存在 |
| 能力审核 / 校验 | `POST /api/v1/capabilities/{claim_id}/review`、`/validate` | admin | API 存在 |
| 证据复核 | `POST /api/v1/snapshots/{snapshot_id}/review-evidence` | admin | API 存在 |
| 覆盖 / 重复候选 | `GET /api/v1/snapshots/{snapshot_id}/coverage`、`/duplicate-candidates` | admin | API 存在 |
| 发布 / 回滚 / 发布记录 | `POST /api/v1/snapshots/{snapshot_id}/publish`、`POST /api/v1/categories/{category_id}/rollback`、`GET /api/v1/categories/{category_id}/publications` | admin | API 存在 |
| 能力合并 / 关系复核 | `POST /api/v1/capabilities/merge`、`POST /api/v1/capability-relations/review` | admin | API 存在 |

ZIP 上传只接受合法文件名的 `.zip`，MIME 限定为 `application/zip`、`application/x-zip-compressed`、`application/octet-stream`，并检查 ZIP 内容签名；没有发现管理员侧的 Word/PDF/Excel 文档上传 API。

### 5.3 前端页面核查

前端只注册以下 6 类售前页面：

| 路由 | 页面 | 用途 |
| --- | --- | --- |
| `#/tasks` | 任务列表 | P2.3 售前工作台 |
| `#/new` | 新建任务 | 普通用户上传 `.docx/.pdf/.xlsx` 做招标参数提取 |
| `#/extractions/{job_id}` | 提取详情 | 查看、修正并启动评估 |
| `#/evaluations/{evaluation_id}` | 评估进度 | 查看评估状态 |
| `#/results/{id}` | 结果详情 | 查看评估结果与导出 |
| 登录页 | 登录 | 输入访问令牌 |

这些页面都不是管理员源码上传、能力审核、证据复核或发布页面。`#/new` 的 `.docx/.pdf/.xlsx` 上传属于普通招标文件提取，不是管理员知识库源码上传。由于没有管理员页面，本说明不提供虚构的管理员页面地址。

### 5.4 启动方式

隔离验收环境已经运行：

- API：`http://127.0.0.1:18080`，容器 `p31-acceptance-api`；
- 数据库：`knowledge_p31_acceptance`；
- 管理员通过 `Authorization: Bearer <ADMIN_TOKEN>` 调用 API；
- Token 只从私有环境读取，禁止写入命令历史、日志、报告或截图。

从代码重新启动（Compose）：

```sh
cd backend
uv sync --frozen
uv run python scripts/generate_env.py
docker compose up --build -d --wait --wait-timeout 180
docker compose exec -T api alembic current
```

API 默认监听 `http://127.0.0.1:8000`；OpenAPI 页面为 `http://127.0.0.1:8000/docs`。端口占用时可设置 `API_PORT=18000`。

本地开发（不使用容器运行 API）：

```sh
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
# 另一个终端
uv run python -m app.jobs.worker
```

前端开发服务器：

```sh
cd backend/frontend
npm install
npm run dev
# http://127.0.0.1:5173/#/tasks
```

前端只提供上述售前页面，不提供管理员页面。

### 5.5 是否需要迁移到 HEAD

本轮没有新增数据库迁移，租户库迁移 head 仍为 `0012`。当前代码的管理员 API 不依赖新迁移；如果后续开发管理员前端，仍需部署到包含 `0012` 的 HEAD 代码，并保留独立验收库。

### 5.6 独立验收环境可用性

- `knowledge_p31_acceptance` 数据库可用，API `18080` 的 live/ready 均可访问。
- 管理员 API 可以在该环境直接调用，但管理员前端不可用，因此只能算 `ADMIN_API_ONLY`。
- 正式库 `knowledge` 仍为 `0007`，本轮未修改；共享部署、正式存储和发布物未触碰。
- 临时 PostgreSQL 专项容器只用于本轮 P2.1/P2.2 专项测试，测试结束后清理，不属于交付环境。

### 5.7 真实资料外发与未授权模型风险

- 管理员 ZIP 上传入口本身只接收归档、计算哈希、登记快照和后台任务，上传元数据记录 `metrics_json.model_mode="NOT_USED"`；本轮没有把真实源码或正式技术文档发送给外部模型。
- 上传后如果管理员继续触发知识调查/构建，系统可能按部署配置调用模型；在模型授权、数据外发批准和计价确认缺失时，不得把真实企业源码或正式技术文档送入未授权模型。
- 本轮业务侧仍为 0 次模型请求、0 Token；B/C/D5–D10 仍保持 BLOCKED。

## 六、未通过、未验收与仍阻塞事项

本轮没有把以下事项改为通过：

- `P31-D05`：括号内建议性阈值仍不单独原子化；主阈值和原文保留。
- `ENV-P1-01`：共享部署仍为旧镜像和迁移 `0007`。
- `DATA-P2-01`：正式库中的企业标签快照/文档仍是演示或人工样例。
- `ENV-P2-01`：验收运行时仍使用既有 `p1.3` 镜像挂载源码，HEAD 镜像重建未完成。
- B1–B10、C1–C4、D1、D5–D10、E4b、G1：真实产品知识、发布物、授权模型、业务复核和真实评估仍缺失。
- 管理员入口没有页面，因此“管理员页面操作验收”不成立；只能记录为 `ADMIN_API_ONLY`。

## 七、安全与证据

- 未写入 Token、密码、API Key 或私钥。
- 真实 Word 全文只保留在本地证据目录，提交的日志只记录哈希、计数、区间、状态和脱敏字段。
- 历史任务不重写、不迁移；旧 199 原子记录继续可读。
- 本轮证据：
  - `docs/acceptance/p31_d04_d06_before_fix.log`
  - `docs/acceptance/p31_d04_multi_threshold_before_fix.log`
  - `docs/acceptance/p31_d04_d06_after_fix.log`
  - `docs/acceptance/p31_d04_d06_regression.log`
  - `docs/acceptance/p31_d04_d06_full_pytest.log`
  - `docs/acceptance/p31_d04_d06_postgres.log`
  - `docs/acceptance/p31_d04_d06_real_retest.log`
  - `docs/acceptance/p31_d06_historical_status.log`
  - `docs/acceptance/p31_d04_d06_frontend.log`
  - `docs/acceptance/p31_d04_d06_secret_scan.log`
- 本地原始响应（不提交）：`storage/p31_acceptance/evidence/p31_d04_d06_real_job.json`、`history_88ea10d9-1e4f-49b6-8e23-7b32dc2cedaa.json`、`history_e8af956e-3982-4072-adb2-319f7da9f82c.json`。
