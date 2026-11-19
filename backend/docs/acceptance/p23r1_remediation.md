# P2.3 第一轮代码审核整改报告（R01～R03 与前端凭证）

- 整改基线：`210967a`
- 整改日期：2026-11-19
- 整改范围：R01 提取页人工修正、R02 下载凭证、R03 导出前固定发布再验证、前端凭证安全
- 不在范围：P1.1～P2.2 历史实现重构、认证/账号体系、企业 SSO、P3.1
- 结论：R01～R03 与前端凭证加固已完成并通过定向测试、全量回归、真实 PostgreSQL 专项、前端类型检查/构建和浏览器实际操作；真实 WAF 业务能力与生产部署仍未验收，不得据此宣称真实产品能力通过。

## 一、整改结论

| 编号 | 整改事项 | 结论 | 主要证据 |
| --- | --- | --- | --- |
| R01 | 提取页支持改参数名/正文、拆分、合并、新增、确认；展示修正前后；保存后重新拉取；保留原文/修正记录/审计；提示影响后续评估 | 已完成 | `test_r01_manual_correction_round_trip_rebuilds_atoms_and_evaluation_count`；浏览器操作记录 `p23r1_browser.log` |
| R02 | 下载凭证明确到期且过期拒绝；令牌不进入 URL；保留身份与任务归属双校验；审计不记录完整令牌；旧导出失败关闭 | 已完成 | `test_r02_download_tokens_expire_refresh_and_never_leak_into_audit`、`test_r02_legacy_export_without_integrity_fails_closed` |
| R03 | 固定 `publication_id`/`snapshot_id`；导出阶段再验证来源文件、引文、哈希与快照归属；失效失败关闭；不改已持久化自动结论 | 已完成 | `test_r03_export_reverifies_source_and_restores_fail_closed`、`test_r03_export_rejects_changed_document_segment`、`test_r03_download_detects_replaced_or_missing_export_file` |
| 前端凭证 | 登出与 401 清除持久化凭证；不写日志/URL；不渲染不可信 HTML；保留企业 SSO 迁移路径 | 已完成 | 代码核对；浏览器登出与 401 实测 |
| 联调缺陷 | 浏览器下载预检缺少 `X-Export-Token` 导致下载失败 | 已修复并回归 | CORS 断言强化；浏览器预检与下载实测 |

## 二、R01 提取页人工修正

### 1. 实现

- 复用 P2.1 已有人工修正能力，不重写提取与原子解析逻辑。
- 新增只读接口 `GET /api/v1/tender/extractions/{job_id}/corrections`，复用 `TenderCorrection` 审计表，支持按参数过滤与数量上限。
- 支持修改参数名称/正文、拆分、合并、新增遗漏参数、确认参数。
- 修改、拆分、合并后写入 `anchor_status`（`manual_edit`、`manual_split`、`manual_merge`）并保留 `original_locator`；页面不再把失效原文锚点当作当前文本锚点。
- 新增参数后统一重排序号，修复新增参数出现 `1000015` 的缺陷。
- 前端新增 `CorrectionDialog`：展示修正前/修正后、拆分前后、合并前后；保存后重新请求服务端提取结果，不在前端伪造原子要求。
- 页面明确提示：人工修正会改变后续 P2.2 评估输入；已经生成的评估结论不会自动更新，必须重新启动评估；原提取内容与本次修正写入不可变审计。

### 2. 验证

- 自动化用例覆盖修改、拆分、合并、新增、确认、序号重排、修正记录动作集合、修正前后内容、重新评估参数数量。
- 浏览器实际操作完成修改（`manual_edit`）、拆分（`manual_split`）、合并（`manual_merge`）、新增两条、确认一条；最终 7 条参数、15 条原子要求、序号 1..7。
- 重新评估后 7 条参数、15 条原子要求，人工修正正文与新增参数均进入评估输入。

## 三、R02 下载凭证

### 1. 实现

- 导出任务新增 `download_expires_at` 与 `file_sha256`；迁移 `0012_export_integrity.py`，可从 `0011` 升级并回退。
- 下载凭证默认有效期 `export_download_ttl_seconds=3600` 秒；创建导出时写入到期时间。
- 新增 `POST /api/v1/tender/workbench/exports/{export_id}/download-token` 重新签发短期凭证；仍校验登录身份、导出任务归属与导出状态。
- 下载接口改为只接受 `X-Export-Token` 请求头，不再接受 URL 查询参数；缺少、格式错误、不匹配均返回 403。
- 凭证过期返回 `EXPORT_TOKEN_EXPIRED`；重新签发后旧凭证立即失效。
- 历史导出任务缺少 `file_sha256` 时视为完整性不可用：详情返回 `integrity_available=false`，下载与重签均返回 409 `EXPORT_INTEGRITY_UNAVAILABLE`，要求重新生成导出。
- 下载前重新计算文件 SHA-256 与生成时记录比对；文件被替换或丢失分别返回 `EXPORT_FILE_TAMPERED`、`EXPORT_FILE_MISSING`。
- 审计只记录令牌指纹（SHA-256 前 12 位）、到期时间与文件摘要，不记录完整下载令牌。
- 前端下载请求把令牌放在 `X-Export-Token` 请求头；收到 `EXPORT_TOKEN_EXPIRED` 时自动重签一次并重试；不把令牌拼进 URL、日志或历史记录。

### 2. CORS 联调缺陷

首次在浏览器点击下载时出现“下载失败”。定位为后端 CORS 允许头缺少 `X-Export-Token`，浏览器预检返回 400。修复 `app/main.py` 的 `allow_headers` 后：

- 预检返回 200；
- `access-control-allow-headers` 包含 `X-Export-Token`；
- 浏览器下载成功，失败提示消失；
- 定向测试中的 CORS 断言已强化为检查该请求头。

### 3. 验证

- 自动化覆盖：正常下载、错误令牌、缺失令牌、过期令牌、重签、旧令牌失效、越权重签 404、审计不含完整令牌、历史导出完整性不可用。
- 浏览器覆盖：Word/Excel 下载成功；页面显示凭证到期时间与完整性摘要状态。
- 原始日志：`p23r1_targeted.log`、`p23r1_browser.log`、`p23r1_exports_validation.log`。

## 四、R03 导出前固定发布再验证

### 1. 实现

- 导出始终使用任务创建时固定的 `publication_id` 与发布哈希，不回退当前生效版本。
- 复用 `PinnedKnowledge` 的证据回读与缓存，新增只读公开方法 `read_verified_evidence()`。
- 新增 `verify_export_evidence()`，在装配导出数据前逐条校验：
  - 证据 ID、来源引用、定位、来源哈希、引文哈希与持久化记录一致；
  - 引文仍以持久化引文为前缀；
  - 证据引用的能力修订属于任务固定发布快照；
  - 固定发布、来源文件、文档分段或引文失效时立即失败关闭。
- 失败返回具体错误码（固定发布/证据不可用、能力修订不匹配、证据不一致），不生成文件、不替换为新发布、不改写已持久化自动结论。
- 下载阶段再次校验文件 SHA-256，避免生成后的文件被替换。

### 2. 验证

- 原始文件被修改、丢失、文档分段改变、发布被篡改时导出失败关闭；恢复后可重新导出。
- 已被替换或删除的导出文件不能下载。
- 导出前后 `ParameterEvaluation`、`AtomicEvaluation`、`EvaluationEvidence` 的结论、证据 ID 与内容未被修改。
- 真实 PostgreSQL 专项覆盖迁移升级、复核、导出、数据库行校验与 `0012 → 0011 → 0010 → 0011 → 0012` 往返。

## 五、前端凭证安全

- 登出统一调用 `clearStoredToken()`，清除 `localStorage` 中的访问令牌。
- API 401 时同时清除内存态与持久化凭证，页面回到登录页并提示“登录已失效”。
- 访问令牌仅放在 `Authorization`；下载凭证仅放在 `X-Export-Token`，不进入 URL、历史记录或日志。
- 未发现 `dangerouslySetInnerHTML` 或 `innerHTML` 使用；页面文本按 React 默认转义渲染。
- 当前仍为令牌粘贴登录，未实现企业 SSO；保留单点登录迁移所需的凭证注入/清除边界，本轮不扩展为 SSO 工程。
- 浏览器实测：登出后 `localStorage` 为空；写入无效令牌并刷新触发 401 后令牌被清除，页面显示“登录已失效”。

## 六、验证结果

| 项目 | 结果 | 原始日志 |
| --- | --- | --- |
| P2.3 定向测试 | `16 passed, 1 warning in 5.50s` | `p23r1_targeted.log` |
| 全量 pytest | `418 passed, 32 skipped, 2 warnings in 72.32s` | `p23r1_full_pytest.log` |
| P1.1～P2.2 回归（排除 P2.3 专项） | `402 passed, 31 skipped, 2 warnings in 66.28s` | `p23r1_p11_p22_regression.log` |
| 真实 PostgreSQL 专项与迁移往返 | `1 passed, 1 warning in 1.49s` | `p23r1_postgres.log` |
| Ruff 格式 | `197 files already formatted` | `p23r1_ruff_format.log` |
| Ruff 静态检查 | `All checks passed!` | `p23r1_ruff_check.log` |
| 前端 TypeScript | `tsc exit=0` | `p23r1_frontend_typecheck.log` |
| 前端生产构建 | 42 modules，JS 296.29 kB，CSS 33.47 kB | `p23r1_frontend_build.log` |
| Word/Excel 内容校验 | 哈希一致、表格/工作表/行数/无公式/无证明材料要求 | `p23r1_exports_validation.log` |
| 浏览器人工操作 | R01 全流程、复核、下载、注销/401 清理 | `p23r1_browser.log` |
| CORS 预检 | 200，`access-control-allow-headers` 含 `X-Export-Token` | `p23r1_cors_preflight.log` |

PostgreSQL 专项指标：`P23_PG_METRICS {"parameter_count":5,"atomic_count":9,"review_count":1,"docx_bytes":40972,"xlsx_bytes":11544}`。

全量测试较整改前基线增加 6 项通过用例：P2.3 定向新增 R01/R02/R03 与旧导出失败关闭用例，CORS 断言在既有用例内强化。

## 七、导出文件内容核对

Word：

- 41577 字节，SHA-256 `2f3c18e1b6424f4b840b420bf61d1ab16d9bc8ff3e767734d35f4647c1451d49`，与数据库记录一致；
- 5 张表；
- 含标题“产品技术参数符合性评估报告”、自动与人工结论分列、人工复核意见、调整理由、人工修正/合并后的参数名称、新增“IPv6 双栈接入”；
- 不含“提供检测报告及功能截图”。

Excel：

- 12920 字节，SHA-256 `3ac3ee85d5cb60e74ff1c6adf4de2e8b0bd6030776327dbc529fd5ec392aec6c`，与数据库记录一致；
- ZIP 完整；
- 四张工作表：评估结果汇总、原子子要求明细、待确认与问题清单、证据索引；
- 汇总表最大行 8，原子明细最大行 16；
- 公式单元格 0；
- 含人工修正/合并名称、复核意见、调整理由与新增参数，不含证明材料要求。

## 八、本轮发现并修复的其他问题

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| CORS 允许头缺少 `X-Export-Token` | 浏览器预检 400，下载失败 | 加入允许头并强化 CORS 断言 |
| 新增参数序号出现 `1000015` | 清单序号异常 | `add_parameter` 后统一重排有效与失效参数 |
| `CorrectionDialog.tsx` 可空参数导致 TS18048 | 前端类型检查失败 | 收窄可空判断，`tsc` 与构建通过 |

## 九、未验收清单

以下事项本轮均未验收，报告中不得表述为已通过：

- **真实 WAF 源码**：缺少获授权的真实雷池 WAF 源码，未验证真实代码提取与证据追溯。
- **真实产品技术文档**：缺少获授权的真实技术文档，未验证真实文档解析质量。
- **真实招标参数**：缺少真实《WAF招标技术参数表.docx》，未验证真实招标参数提取与结论准确率。
- **授权企业模型**：缺少获授权的企业模型接口与凭据，语义判断仍为降级/合成模式。
- **生产部署**：未验证生产反向代理、TLS、WAF、容量、并发、备份恢复与正式安全渗透。
- **真实账号与 SSO**：未实现账号、角色、组织架构与企业 SSO，仅保留迁移边界。
- **多浏览器与性能**：只验证当前内置 Chromium；未做浏览器矩阵、导出并发与容量压测。
- **真实业务验收**：未由业务方提供资料并组织独立复核，不能宣称真实 WAF 参数评估能力通过。

## 十、结论

R01～R03 与前端凭证加固已完成，未发现未整改的阻断项；本轮可以提交人工代码审核与合成环境验收。由于真实资料、真实模型与生产环境均未提供，真实业务能力、生产安全与容量仍未验收；在完成独立真实业务验收前，不应对外宣称“真实 WAF 参数评估能力通过”，也不应启动 P3.1。
