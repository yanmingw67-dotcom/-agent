# P1.4 第一轮整改验收报告

- 日期：2026-11-17
- 审查基线：`9eb697fb48daaaa155b6353a12d00fce27fcfa2d`
- 范围：P14-R01、P14-R02、P14-R03；不开发P1.5或Agent B。
- 结论：本轮指定整改及验收通过，提交第二轮审核；不等于全部产品语义、企业规模或生产性能验收通过。

## 1. P14-R01：真实503根因及修复

真实PostgreSQL复现得到 SQLSTATE `P0001`、SQLAlchemy `ProgrammingError`、驱动 `RaiseException`。实际触发函数为 `guard_active_snapshot`，对应0001既有 `category_snapshot_guard` BEFORE触发器：生效快照必须同品类且已为published。

旧发布流程把快照状态与品类指针同时留待ORM flush；实际品类UPDATE先发生，触发器当时仍看到extracted快照，因而抛错。旧统一异常映射返回503 / DATABASE_UNAVAILABLE，**这不是数据库不可连接的证明**。

修复仅调整同一事务内的落库顺序：创建冻结清单 → 写快照published并flush → 切换两个品类指针与切换审计 → 由API提交。中间flush不是commit，没有引入分段提交。数据库约束、触发器及0001～0007迁移未修改，无吞异常、删库、重置或禁用保护。

诊断日志只记录correlation_id、异常类型、SQLSTATE、约束/表/列标识和允许的触发函数名，不记录SQL、参数、请求体、凭据、异常正文或DETAIL。本次RAISE没有设置constraint字段，**constraint_name如实为null，不伪造约束名**。

- 原失败correlation_id：`d1bb9e07-480c-4edb-bd4f-625b91b4e6e2`
- 新真实HTTP诊断correlation_id：`eb5f7a08-11d5-4607-9383-28f29457dd47`
- 证据：`diagnosis-before.json`、`api-diagnostic-failure.json`、`preserved-postgres-guards.json`。
- 复现事务强制rollback；复现前后全部受保护历史行和生效指针一致。

## 2. P14-R02：合并并发一致性

合并先读取候选不可变作用域定位，再按“品类 → 同一快照”的统一顺序加锁；取得锁后才读取双方最新审核修订、校验source_revision和target_revision、核验接受状态及边界一致性。不跨快照合并，不扩大范围。

两个真实独立Session的测试分别覆盖源侧与目标侧审核更新：第一事务持锁并新增退回修订，第二事务发起合并。使用线程事件及PostgreSQL `pg_blocking_pids`验证真实锁等待，再提交第一事务；第二事务恢复后返回MERGE_REVISION_CONFLICT，未生成merged修订。不是用SQLite或mock锁模拟并发通过。

## 3. P14-R03：锁后对象刷新

品类与快照锁查询显式populate_existing，并禁止锁前autoflush陈旧对象；快照所属品类用标量查询定位，不依赖Session缓存。最新审核修订查询也显式刷新，发布所选修订重新加载。回滚复用品类锁刷新逻辑。

测试持有expire_on_commit=False的旧Session对象，另一事务修改状态或发布后，验证：
- 快照status或index_status变为不就绪时准确拒绝；
- 品类两个指针及快照published状态同时刷新；
- 新审核修订使旧审核版本合并失败；
- 并发发布不能用旧expected指针覆盖新发布；
- 旧Session不能以缓存指针回滚新发布。

保证以当前实际PostgreSQL READ COMMITTED和统一应用锁协议为边界，不宣称任意隔离级别或绕过服务直接修改数据库也具有同样语义。

## 4. 真实API + PostgreSQL验收

继续原失败检查点，复用既有已接受修订 `60108a33-e894-44ee-9034-c85e050fb6b7`，不重新跑原验收脚本、不清空已审核数据。入口与筛选机制只新增明确窄化的开发方审核修订；源码原文逐条回读。不是模型自动批准，不是独立人工用户签字。

快照S：`d672efe9-5dec-44f6-8fb2-d305c3b015db`

发布P1：`1af83dd5-e04e-4517-a81c-ab8a15201280`

发布P2：`db315185-3b36-4c8f-b9a4-3a7e055a1369`

| 阶段 | active_publication_id | active_snapshot_id | 可见能力 | 发布记录/切换记录 |
|---|---|---|---:|---:|
| 发布前 | null | null | 无 | 0 / 0 |
| 首次发布 | P1 | S | 1 | 1 / 1 |
| 第二次发布 | P2 | S | 3 | 2 / 2 |
| 过期指针发布失败409 | P2 | S | 3 | 2 / 2 |
| 未确认覆盖缺口失败422 | P2 | S | 3 | 2 / 2 |
| 回滚后 | P1 | S | 1 | 2 / 3 |

P1只含单次调用预览首项机制；P2增加模式分发及有条件筛选机制，含一项已审核depends_on关系。两版本均保留覆盖未知及局部能力边界。普通用户不能发布或指定后台快照。

P1清单哈希：
`877338d1623d38d6e913b19634f57dee69183e28c5afa9069028612b501d0f90`

P2清单哈希：
`2ba20577968bff3844d8cce6eabb017defe93769307b17096d2ac3cd966a3bfd`

回滚响应与首次发布后的响应逐字段完全相等，哈希恢复为P1值。冻结清单及其哈希在新增审核、失败发布和回滚后均未改变。最终业务品类保持P1生效，历史P2保留，不额外恢复P2。

完整请求、响应、correlation_id、源码证据、每阶段数据库指针、完整冻结payload和哈希对照见 `real-api-postgres-success.json`；汇总见同名log。

另在真实PG随机隔离schema测试跨快照首次/第二次发布和回滚，验证snapshot指针实际从S1→S2→S1，不只验证同快照内清单切换。

## 5. 原子失败与测试

| 检查 | 结果 | 证据 |
|---|---|---|
| 原194项 + 新增本地10项 | 204 passed，2 warnings | pytest-full-first.log |
| 真实PG专用8项 | 8 passed | postgres-concurrency-first.log |
| 定向原P1.4 + 脱敏日志 | 35 passed | pytest-focused-first.log |
| 增量锁与诊断 | 10 passed | pytest-remediation-first.log |
| Ruff静态和格式检查 | 通过 | static-checks.log |
| 真实迁移 | 0007 (head)，无新增upgrade差异 | alembic-current.log / alembic-check.log |

本地pytest中的8 skipped是需真实PG的显式隔离用例；它们已在实际PostgreSQL独立执行通过，不计作本地通过。合计验证原194项及新增18项。

真实PG故障注入仅置于随机测试schema：
1. publication_switch INSERT即时报23514；
2. DEFERRABLE约束在commit时报23514。
两类故障均发生在已有清单插入、快照状态写入及指针赋值之后；回滚后旧指针、快照状态、发布清单与切换数量全部和故障前一致，没有半成品发布。生产public schema未注入故障触发器，测试schema运行结束已清理。

## 6. 数据与历史保护

- 基线受保护文件298个逐字节一致：原测试、0001～0007迁移及所有历史报告/失败日志。
- 以 `historical-files-preserved-verified.json` 为准。初版清单因子目录相对路径筛选得到0项，已保留并明确作废；修正版使用full-tree和非空断言，未把空检查算作通过。
- 实际数据库基线包含20候选、3审核修订、3审核事件、15调查记录、442轨迹、12证据、2原关系；逐行哈希验证原行未删改。新增审核和发布记录追加保存。
- 源码与文档证据沿用既有来源哈希核验；没有执行上传源码，没有额外收费模型调用。
- 不更换数据库，不重写迁移，不用重新生成候选掩盖问题。

## 7. 资源与安全

七服务健康，无OOM；应用容器继续只读根文件系统、非root、cap_drop=ALL和no-new-privileges。源码读取卷保持只读；worker仅按原设计写解包卷。单builder，独立retrieval服务，企业外发关闭。

容器配额总计4 CPU / 6144 MiB，维持4C8G目标范围内的原配置。Docker宿主实际8 CPU / 8,321,798,144 bytes；**不是独占4C8G整机证明**。即时采样不是负载测试，不宣称企业规模/生产吞吐通过。

实际容器中5个生产修改文件SHA256与工作区一致。健康检查、资源采样、安全配置和源码指纹见 `resource-security-runtime.json`。提交前秘密扫描、.env忽略及0600检查见 `security-scan.json`。

## 8. 交付与后续边界

本次修复只涉及发布写序、并发读取/刷新、脱敏日志及独立新增测试与验收工具。原 `scripts/p14_acceptance.py` 保留；新恢复脚本拒绝覆盖已有验收文件。不要盲目再次执行产生真实副作用的验收脚本。

本轮完成后按用户授权推送GitHub，暂停等待第二轮审核。未开始P1.5或Agent B。关键词门槛不是完整语义证明，产品支持与运行时接线仍需独立人工确认。
