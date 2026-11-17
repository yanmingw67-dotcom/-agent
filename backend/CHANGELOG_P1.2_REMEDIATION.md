# P1.2 第一轮审核整改变更

基线eb18df6ba50ee4472efea0ce6acf277f1bb06dde；仅P1.2，不进入P1.3。
本轮本地提交号见交付回复。原98项测试、0001～0003迁移和原始reports保持原文。

## 已实现

- R01：UTF-8/UTF-16/32 rg每文件上限改为全局cursor+limit+1，
  UTF-8禁用不确定并行文件顺序；全局offset与实际返回条数推进，超时显式未穷尽。
  UTF-16/32 rg输出落受控临时文件逐行读取，避免大JSON一次驻留内存。
- R02：snapshot.index_status仅描述源码，文档任务的运行、完成、失败回调、失联和retry
  不覆盖源码状态。0004迁移按最近source_index任务修复旧误标，不改发布指针。
- R03：PDF逐页记录parsed/empty_page/ocr_required；保留其他成功页，
  文档partial可搜索/准确回读并提示覆盖缺口；无文本整份仍ocr_required。
- R04：引用候选按文件路径/行号/边ID稳定排序和正确offset，返回实际边ID便于验证分页。
- R05：Sheet合并范围在documents.parse_details保存一次；单元格只保留所属合并范围，
  仍保存公式原文且不求值。
- coverage_incomplete独立于truncated，明确大文件/异常编码/未知语言AST/未建索引/未完成文档缺口，
  防止无命中误判产品能力不存在。
- 真实PostgreSQL非空英文文档搜索暴露旧TextClause位或组合TypeError，
  改为SQLAlchemy or_，保留首次失败日志并以六服务验收覆盖。
- parser version p12-2，暂存重建可修复旧解析结果；不自动修改已发布快照。
- 基准支持REPORT_DIR，所有本轮日志仅写reports/p12-remediation。

## 未完成/待验证

- R06：无独立4C8G VM和结构真实脱敏产品库，仍需部署方提供并复跑；不冒称完成。
- R07：流式大文件正文检索/片段回读暂缓，2MiB上限保持；本轮只补明确覆盖缺口，
  不把oversized当完整内容检索。
- 页状态基于可提取文本和图像存在性，非完整页面语义识别；OCR未实现。
- offset深分页可能变慢，超时cursor不前进必须缩小范围，不承诺全库任意深分页SLA。

## 交付

新增专用反例测试文件、人工PDF/XLSX构造器和真实六服务整改验收脚本；
完整报告P1.2_REMEDIATION_REPORT.md，精确变更清单FILE_MANIFEST_P1.2_REMEDIATION.txt。
本轮不更改共享基础设施/鉴权体系/发布逻辑，不伪造能力记录。
