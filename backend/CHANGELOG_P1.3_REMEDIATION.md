# P1.3审核整改变更

基线9d91986；审核附件P1.3_Code_Review_2026-11-17 (1).md。仅本地整改，不进入P1.4。

## P13-R01/R02/R04：有限模型任务与逐条检查点

- 新bounded_builder：缩小输入为模块内有限源码；Pydantic AI ToolOutput紧凑能力结构，
  模型只选一条能力、机制、限制与行范围，服务端复用共享read_source与register_evidence验证。
- 每条候选及链接独立落库，后续候选/等效调用超时保留已有成果并标partial；
  retry跳过已保存槽位、预算不重置。保存函数核对既有候选一致性，防止覆盖或重复插入。
- 等效假设独立后置，只允许引用已保存局部key，始终hypothesis；
  不代表语义正确，人工评估问题保留在报告中。
- runtime保留模型/工具/Token/上下文预算和脱敏审计，新增终端输出工具名；
  明确输出截断/模型超时错误码。不扩大4000/30000 Token或60秒请求上限，不切换供应商。
- 旧explore模式保留兼容，本轮推荐及真实验收使用显式bounded；不宣称原大范围探索已稳定。
- schema新增调查模式、1～3条上限、后置等效开关，原请求字段与数据库能力结构兼容。

## P13-R03：可信来源分类

- 0006仅追加snapshot/document默认enterprise字段，所有旧记录不自动降级。
- classification服务读取源码和文档最高敏感级别；调查请求自报分类只审计。
  worker不信任务旧metrics，运行前再次从数据库判断出站授权。
- admin专用approve-public-fixture只允许审阅过的固定人工资产，核对文件集合/内容哈希、
  文档原文哈希并重新读取源文件；任意修改/额外资料拒绝标public。
- 审批写管理员审计，上传企业文档默认enterprise，不继承public标签。
  企业外发开关仍false，未发送企业源码，不提供任意降级接口。

## 验收材料

- 新tests/integration/test_p13_remediation.py：企业伪造public拒绝出站、
  企业文档最高级别、人工清单/权限/审计/篡改、单候选落库、
  后续超时保留/重试不重复、模型不可用等无外网故障回归。
- 新scripts/p13_remediation_acceptance.py：真实端点/API/队列/PG专用验收，
  没有FunctionModel/预置草稿；每轮独立保存不覆盖；数据库查询和API再次证据回读。
- reports/p13-remediation：真实成功/用量/数据库轨迹、失败诊断、回归、人工机制与误判评估。
- docs/P1.3_REMEDIATION_PLAN.md：事前门槛；RUNBOOK：新入口与保守边界。
- P1.3_REMEDIATION_REPORT.md：实际结果与未覆盖项。旧验收报告不改，README指向新报告。

原138项测试、0001～0005和全部既有历史日志保持原文；新报告原始CLI日志保留尾空白。
密钥仅Git忽略的本地.env，代码/日志/报告交付前做真实字节扫描；不输出秘密。
不实现Agent B、语义批准、发布、投标结论或独立4C8G验收，不自动推送。
