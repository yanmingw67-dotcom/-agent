# P1.3 变更与审阅入口

基线：6f19e3de68a0239da63810ca2dcbe59f5c2f8688。仅本地提交，等待明确推送授权。

## 实现

- 新增0005六张知识调查表：模块检查点、真实来源证据、候选能力、证据链接、关系假设、脱敏轨迹。
- 新增knowledge_builder/runtime：锁定Pydantic AI工具循环、结构校验、只读检索封装、
  完整回读登记、请求/工具/Token/上下文/时间预算、累计重试计量与成功模块复用。
- 新增evidence_registry/service/schemas：确定性证据ID、来源/哈希/范围/快照反伪造、
  原子候选保存、仅文档/仅测试降级、语义始终未验证、关系始终hypothesis。
- 新增investigations管理路由：显式异步调查、模块输出、轨迹分页、候选与证据查询；
  原build仍仅索引，retrieval服务不注册新增知识业务写路由。
- queue/worker/jobs/main/config/Compose新增独立低并发builder；与indexer分别使用全局锁；
  builder只读源文件和文档卷，不挂归档或控制卷；七服务总配额4CPU/6144MiB。
- 锁定pydantic-ai-slim[openai]==2.69.0、pydantic==2.14.3及uv.lock，
  旧试装版本因opentelemetry导入失败已弃用，诊断日志保留。
- 真实模型定位错误反例修复：404/422定位失败反馈ModelRetry，权限/来源哈希变化/预算仍终止；
  模型专用紧凑envelope不影响P1.2 HTTP响应；ID只取真实检索结果。
- 同一任务各模块共享异步Runner，避免模型连接池跨已关闭事件循环；
  官方支持的思考强度可显式配置，不暗中关闭思考或提高总Token预算。
- 模块名无文档命中时最多回退同快照5条文档候选，并标相关性和覆盖率未验证。
- 新增独立回归文件、人工公开资产/15机制标注/负例、真实模型验收与明确标注FunctionModel的演示；
  密钥只在Git忽略的0600本地.env，不进入镜像、报告或提交。

## 材料与保留

运行说明：docs/P1.3_RUNBOOK.md；架构：docs/P1.3_ARCHITECTURE.md；
计划：docs/P1.3_IMPLEMENTATION_PLAN.md；结果：P1.3_ACCEPTANCE_REPORT.md。
精确文件清单：reports/p13/delivery-files.txt。
所有原115项测试与0001～0004迁移保持原文，原P1.1/P1.2及整改日志不覆盖；
新回归在reports/p13子目录。诊断失败日志保留，不把它们列为通过记录。
旧P1.1/P1.2验收脚本产物含历史模式标签（如六服务/NOT_IMPLEMENTED），
其回归只验证原功能，不代表当前P1.3能力状态。

## 严格停止点

无Agent B、正式审核/发布、语义已验证状态、投标结论、自动归并、Probe或源码动态执行。
不把人工小样例效果外推为企业召回率；不把8CPU Docker主机的容器配额当作独立4C8G VM验证。
实际模型成功/失败、Token、费用口径与未覆盖项以验收报告为准；不进入P1.4。
