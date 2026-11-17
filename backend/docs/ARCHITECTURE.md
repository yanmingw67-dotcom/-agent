# P1.1 架构与安全边界

## 调用路径

管理员 → 请求体解析前鉴权 → API 分块接收 ZIP → 归档哈希
→ 数据库事务（快照 + queued 任务 + 审计）→ 返回 202
→ Worker outbox 投递 Redis/RQ → validating → 安全解压 → extracted / failed。

API 不执行解压、索引或模型分析。模型与后续检索模块当前为 NOT_IMPLEMENTED。
快照内部 ID 不在售前品类接口暴露；上传与任务接口是管理员后台接口。

## 数据库

本批表：product_categories、source_snapshots、knowledge_jobs、audit_events。
任务表兼作 outbox，避免数据库提交成功但 Redis 投递失败导致任务丢失。
同品类 queued/running 任务通过部分唯一索引互斥，上传和重试采用品类行锁。
只有发布且属于该品类的快照才能成为生效指针，由 PostgreSQL 触发器保护。
快照的品类、归档哈希及归档位置不可修改。发布流程本批未实现。

任务 status 是执行结果（queued/running/succeeded/failed），stage 是处理阶段。
P1.1 的 succeeded 仅表示导入阶段完成，stage=extracted；知识库仍未就绪。
重试沿用同一快照和任务 ID，attempt 递增，只允许 failed；不可变非法 ZIP 重试仍失败。

## 可靠投递与恢复

Worker 每轮先扫描 outbox，投递 RQ 后保存随机 dispatch_token。
处理器按 job_id + token + queued 状态加锁领取，重复投递不重复处理。
数据库提交前 RQ 消息已发出但事务失败时，旧 token 不可领取；outbox 可重新投递。
未领取消息超过 job_timeout_seconds+60 秒后可重新投递；
运行任务超时失联后标记 WORKER_LOST，允许管理员显式重试。
RQ 失败回调提供 WORKER_INTERRUPTED 兜底。
工作进程 fork 前清空 SQLAlchemy 连接池，不跨进程复用数据库 socket。

解压先写新临时目录，校验完成后原子重命名提交。
如果文件系统已提交、数据库尚未提交就崩溃，重试先重新核对归档及已提取文件哈希。
不做业务内容去重；同归档再次上传会创建新的内部快照。

## 文件权限与部署取舍

API：归档卷可写，源码卷只读；无公开源码静态链接。
Worker：归档只读；导入路径 INGESTION_ROOT 可写；同卷 SOURCE_ROOT 只读挂载。
导入处理器是受信写入组件，不是分析 Agent；只读视图是后续工具读取入口。
同一导入容器仍能通过 INGESTION_ROOT 写入，因此这不是对整个 Worker 的强只读隔离。
引入 Agent A 时必须将分析进程与导入写入进程隔离，并只给分析容器挂载只读源码。
当前没有分析进程，也没有向模型开放任何文件系统或执行工具。

文件为 0440、完成目录为 0550，容器非 root，根文件系统只读，移除 capabilities，
启用 no-new-privileges，限制内存/CPU/PID 和任务执行时长。
操作系统管理员及导入进程仍是受信边界，文件权限不是防主机管理员篡改机制。
生产环境应在 TLS 反向代理后运行，本机只绑定 127.0.0.1。

## ZIP 策略

- 流式上传，文件 100 MiB；请求体额外允许 1 MiB multipart 开销。
- 默认最多 20000 条目、单文件 20 MiB、总展开 500 MiB、比率 100、深度 20。
- zipfile 解析前流式预检中央目录条目，避免海量空条目先分配对象。
- 支持普通单卷 ZIP（stored/deflate），保守拒绝加密、多卷、ZIP64 及其他压缩算法。
- 拒绝绝对/穿越/反斜线/控制字符路径、链接、特殊文件、大小写及 Unicode 规范化碰撞。
- 检查声明限额和实际读取量，CRC 校验，排除文件仍受所有展开限额约束。
- 二进制（首块含 NUL）和常见依赖/生成目录被排除；manifest 记录哈希、原因。
  二进制检测为保守启发式，不保证识别所有二进制或生成文件。
- 敏感字符串检测只记录布尔标记和数量，不写入匹配原文。本批不存在模型读取工具；
  P1.2/P1.3 必须实现工具输出前的屏蔽/访问策略，不能把此检测当作完整 DLP。
- 从不执行 ZIP 中代码、脚本、安装指令或 README/AGENTS 指令。

## 日志与鉴权

所有 API 错误为 error_code/message/correlation_id。
请求关联 ID 由服务端生成，日志只含事件类型、方法、状态和耗时，不记请求体和查询参数。
导入审计记录品类建立、上传、重试；RQ/任务记录后台执行结果。
Bearer Token 必须独立且至少 32 字符；生成脚本用安全随机数生成，拒绝覆盖既有 .env。
角色级最小鉴权不等于企业用户系统：当前只记录 admin/read 主体，不支持个人问责或 SSO。
