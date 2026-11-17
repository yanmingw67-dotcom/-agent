# CHANGELOG P1.2 · 源码与技术文档索引引擎

基线 dafcea7；本批不实现 Agent A 自主挖掘、Agent B 或正式投标应答。
本地提交号以本批交付回复和 git log 为准。

## 实现

- 新迁移0003：文件/符号/语法引用、文档/分段、模块/入口，快照独立index_status，
  PostgreSQL文档FTS GIN索引。旧迁移0001/0002不改写。
- source_index/document_index通过原durable outbox异步投递到独立单任务indexer；
  逐文件事务检查点、哈希/解析器版本复用、失败/partial显式重试。
- Tree-sitter Python和Go：函数、类/类型、接口、方法；未知语言unsupported但可词法检索。
  语法错误、异常编码、超大与排除文件明确状态；测试文件仅角色标记，不当能力事实。
- DOCX/PDF/XLSX/MD/TXT异步解析，段落/章节/表格/页/bbox/Sheet/单元格/合并定位；
  Office容器安全检查、公式不执行、外链不跟随、扫描PDF ocr_required。
- 共享ReadOnlyTools，服务端冻结RunContext，强类型ToolRequest/envelope；
  模块/上下文/入口、批量rg正文搜索、符号/路径、准确行号/哈希回读、
  未解析引用候选、文档全文/分段回读；UTF-8和带BOM UTF-16/32搜索。
- 敏感文件/分段默认不返回；限查询大小/结果数/读取行数/上下文/正则容量/搜索时间。
  普通read只用生效指针；后台快照仅admin暂存调查，不提前实现发布。
- 六服务Compose：api、worker、indexer、retrieval、postgres、redis；
  indexer仅只读源码/文档，不挂载归档；独立retrieval默认DB只读事务且不注册写接口。
  CPU上限合计4核，内存合计5376MiB；默认单索引Worker带Redis租约。
- 基础进程HTTP/检索/SQL延迟指标、任务耗时/CPU/RSS采样，真实并发检索资源基准。
- flock容量预留/已有用量检查/最小磁盘空间、失败释放，孤儿/残余预留只读报告；
  不自动删归档、快照或证据，不冒称文件系统级配额。
- 人工公开跨模块源码与真实DOCX/PDF/XLSX资产；新增测试及运行文档。

## 真实测试与证据

当前完整测试结果以 reports/p12/pytest-final.log 为准（本轮98项）。
原P1.1 70项测试未删除；Mock只用于既有故障/时序注入，不用Mock冒充解析索引。
六服务真实异步验收、P1.1容器冒烟回归、迁移一致性、资源与P50/P95见
P1.2_ACCEPTANCE_REPORT.md 和 reports/p12/。

## 未实现/已知限制

- 真正独立4C8G虚拟机实测待部署方复跑；开发侧是8CPU主机上4CPU总容器配额。
- 无跨快照解析缓存；增量是同快照逐文件检查点/失败重试/相同哈希版本复用。
- 模块/入口/调用关系是候选，不保证运行时可达性或精确跨模块调用图。
- OCR、复杂嵌入文档、全部编码适配、所有语言AST、向量索引未实现。
- 存储限制不含DB/Redis/镜像；崩溃残余预留需要人工核对；自动清理与审批留后续。
- 敏感检测非完整DLP；强企业用户权限/TLS等仍需部署策略。
- 证据ID、能力记录、关系审核发布和Probe按后续批次实现，不伪造完整能力链路。

## 变更文件

完整新建/修改清单见 FILE_MANIFEST_P1.2.txt，按批次归组：
- 配置/部署：.env.example、Dockerfile、docker-compose.yml、pyproject.toml、uv.lock。
- app/db：index_models.py、models.py、session.py、migrations/env.py、0003_static_index.py。
- app/ingestion：capacity.py、text.py、language_adapters.py、doc_parsers.py、indexer.py、
  resources.py；content_store.py增加容量接入。
- app/jobs：queue.py、worker.py、handlers.py支持隔离队列/状态与容量。
- app/api：indexing.py、retrieval.py；jobs.py、knowledge_uploads.py兼容新任务/容量。
- app/core：metrics.py、config.py、logging.py；app/main.py服务模式与错误/指标。
- app/retrieval：schemas.py、tools.py、__init__.py。
- tests：新增P1.2索引/隔离/文档/编码/恢复/生效夹具/容量并发测试；fixtures/p12真实格式资产。
- scripts：generate_p12_fixtures.py、p12_acceptance.py。
- docs：P1.2_IMPLEMENTATION_PLAN.md、P1.2_ARCHITECTURE.md、P1.2_RUNBOOK.md；
  初始计划/架构/存储规划增加当前批次导航。
- README.md、本CHANGELOG、P1.2_ACCEPTANCE_REPORT.md、FILE_MANIFEST_P1.2.txt。
- reports/p12：实际各轮检查、失败诊断、成功部署、基准JSON/原始样本与回归日志。

交付后暂停P1.3，等待正式审核。原P1.1及整改reports原始文件不改写。
