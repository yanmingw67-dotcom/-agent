# P1.4第一轮整改运行说明

## 当前状态

真实业务数据库迁移版本0007，原数据保留；已完成两次发布和回滚，首个发布P1保持生效。详见REMEDIATION_REPORT.md和real-api-postgres-success.json。

## 普通回归

在backend目录运行：

```sh
uv run pytest -q
uv run ruff check app scripts/p14_r1_diagnose.py scripts/p14_r1_acceptance.py tests/integration/test_p14_round1.py tests/infrastructure/test_p14_r1_postgres.py tests/unit/test_p14_r1_diagnostics.py
```

普通pytest不启动PG基础设施；8项PG专用测试显式跳过。

## 真实PG隔离测试

在已有Compose环境运行：

```sh
docker compose exec -T -e P14_RUN_POSTGRES=1 api python - < tests/infrastructure/test_p14_r1_postgres.py
```

该测试仅在随机p14_r1_test_前缀schema迁移0001～0007、创建人工固定数据和故障触发器，最终清理该专用schema。不会重置public数据库。需要CREATE SCHEMA权限；不应向生产增加权限只为测试。若测试进程被强制中断，先核对残留schema所属运行，禁止批量删除未知schema。

真实并发采用两个Session、不同连接、事件同步及pg_blocking_pids确认阻塞；不以固定等待替代数据库锁证据。

## 真实验收脚本

`scripts/p14_r1_acceptance.py`已经执行成功，含真实审核/发布/回滚副作用，输出文件存在时拒绝执行。不要删除输出文件或改用新路径从头重跑。需要继续时应读取数据库实际修订和指针，独立设计增量恢复步骤。

`scripts/p14_r1_diagnose.py`提供真实发布诊断，始终rollback；它不是正常发布入口。修复后不再应复现旧P0001。历史复现结果已保存，不覆盖。

## 排查数据库错误

按客户端响应或X-Correlation-ID关联knowledge.audit的database_error结构化记录：
- sqlstate、exception_type、driver_exception_type；
- constraint_name/table_name/column_name可能为null；
- function_name可定位既有保护触发器。
不需要开启SQL echo、打印异常正文、请求体、SQL参数或.env。503只是既有API错误映射，不可仅凭503断言数据库断连。

## 迁移与上线

本轮不新增迁移，0001～0007不得改写。部署后核对alembic current/check和容器内源码哈希。禁止删除数据卷、禁用触发器或清空审核记录。

保留4 CPU/6144 MiB配额、单builder、独立检索服务、企业外发关闭。密钥只在Git忽略且0600的.env中，不提交日志或报告。

完成后等待第二轮审核，不进入P1.5。
