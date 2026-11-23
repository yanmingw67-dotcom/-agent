# P3.1-B 真实 WAF 源码 ZIP 导入根因与修复记录

- 验收日期：2026-11-23
- 验收环境：Compose 项目 `product-knowledge-p31b`
- 源码归档：`b0e41631-20d0-494a-bde8-7cc2a5ad415a.zip`
- ZIP 大小 / SHA-256：`783,596,867 B` / `cf3c8c88fb4f48ce5219cd0d0a0ae4841638acec75a5a69d429129565a67fcbf`
- 品类：`waf`（`69659364-0781-4038-abc7-d01f3dfa6594`）
- 快照：`31675257-2c36-46cf-986f-da166ee2381d`
- 失败任务：`0324f27e-cd22-4a1a-aeec-323283a3effa`，`job_type=source_import`
- 最终状态：任务 `succeeded / extracted`（`attempt=2`）；快照 `status=extracted`、`index_status=indexed`；索引任务 `0bdba68d-72eb-4a70-9008-7c74bdbff9d9` 为 `succeeded / indexed`

## 一、明确根因

失败不是容量不足，也不是 ZIP 损坏，而是 **归档路径/排除策略比真实源码更严格**。首次失败错误码为 `UNSAFE_PATH`，耗时 756 ms，`model_requests=0`。

按原策略执行时存在三个依次触发的阻断点：

1. **冒号被一律当作不安全路径**：真实 ZIP 中有 4 个文件名含冒号：
   - `frontend/scripts/ci:publish`
   - `frontend/scripts/ci:build`
   - `Dockerfile-postgres:11.16-wal-level-logical`
   - `Dockerfile-golang-scantist:1.13.15`

   Linux 文件名允许冒号；这些是合法构建脚本/镜像说明文件。旧逻辑用 `":" in part` 拒绝，导致 `normalized_path()` 抛 `ArchiveRejected("UNSAFE_PATH")`。

2. **符号链接被当作致命错误**：真实 ZIP 有 6 个符号链接，目标均为相对路径，且目标不在 ZIP 内。旧逻辑在条目检查阶段抛 `LINK_OR_SPECIAL_FILE`。即使修复冒号，仍会在这里失败。

3. **缓存文件在排除前先命中单文件上限**：`.git/objects/pack/*.pack` 中最大文件为 `758,845,614 B`，超过 128 MiB 单文件上限。旧逻辑先做 `file_size > max_file_bytes`，再判断 `.git` 排除，因此会抛 `FILE_SIZE_EXCEEDED`。该文件属于版本库缓存，不应解压或索引。

此外，ZIP 的 6,171 个条目全部没有设置 UTF-8 标志（`0x800`），Python 会按 CP437 解码，中文目录名显示为乱码。它不导致失败，但会让源码路径错误，因此一并做 **CP437 → UTF-8 还原**。

## 二、为什么不是容量或 ZIP Bomb

| 指标 | 实际值 | 限额/结论 |
| --- | --- | --- |
| ZIP 大小 | 783,596,867 B | < 1 GiB |
| 条目数 | 6,171 | < 20,000 |
| 目录 / 文件 | 1,282 / 4,889 | 正常 |
| 解压总量 | 843,340,256 B | < 4 GiB |
| 最大压缩比 | 34.54 | < 100 |
| 加密条目 | 0 | 不触发加密拒绝 |
| 最大单文件 | 758,845,614 B（`.git` pack） | 属缓存排除项，不应参与单文件预算 |

因此容量、条目数、总量和压缩比均未超限；根因是路径与排除判定顺序。

## 三、最小安全修复

修改 `app/ingestion/archive_guard.py`：

1. **冒号规则收紧但不过度拒绝**：允许非盘符冒号（如 `ci:publish`），继续拒绝 `C:/...`、任何单字母盘符前缀、绝对路径、`..`、反斜杠、NUL、控制字符、尾随点和尾随空格。
2. **符号链接/特殊文件只记录、不落盘**：条目标记为 `LINK_OR_SPECIAL_FILE` 排除项，不读取目标内容、不创建链接、不跟随链接；`source_files` 以 `parse_status=excluded` 保存审计记录。
3. **缓存/依赖项先排除再算预算**：`.git`、`node_modules`、`__pycache__`、`__MACOSX`、`.venv`、`venv`、`vendor`、`dist`、`build` 以及 `.DS_Store`、`Thumbs.db` 不读取、不解压、不参与单文件/总量/压缩比预算；仍受上传大小、条目数、路径和归档结构校验。
4. **CP437 文件名还原**：未设置 UTF-8 标志时尝试 `CP437 → UTF-8`，失败则保留原解码；还原后重新执行全部路径安全校验。
5. **保留全部负向校验**：路径穿越、ZIP64 结构、多卷、加密、压缩方式、条目数、文件/总量/压缩比、路径碰撞、哈希完整性均未取消。排除项写入空内容哈希仅满足 `source_files.sha256` 非空约束，检索层按 `parse_status=excluded` 跳过，不回读原文。

## 四、真实归档验证结果

使用修复后代码对同一归档做只读解压（不写数据库、不标记任务）：

| 项目 | 结果 |
| --- | --- |
| 条目数 | 6,171 |
| 保留文件数 | 4,743 |
| 排除数 | 146 |
| 排除原因 | `dependency_or_generated=87`、`binary_or_unknown_encoding=53`、`LINK_OR_SPECIAL_FILE=6` |
| 读取总量 | 65,727,825 B |
| 实际落盘 | 33,057,383 B |
| 敏感内容命中 | 141 个文件（仅记录标记，不输出正文） |
| 耗时 | 6.29 s |
| 临时目录 | 已清理 |

随后通过既有 `POST /api/v1/jobs/{job_id}/retry` 重试原失败任务，未重新上传 747 MB 文件：

| 项目 | 结果 |
| --- | --- |
| 导入任务 | `attempt=2`、`succeeded / extracted`，4,450 ms |
| 快照 | `status=extracted`、`index_status=indexed` |
| 导入指标 | `file_count=4,743`、`excluded_count=146`、`total_bytes=65,727,825`、`sensitive_files=141`、`model_requests=0` |
| 索引任务 | `succeeded / indexed`，45,823 ms，`indexed_files=4,889`、`reused_files=0`、`model_requests=0` |
| 资源采样 | CPU 32.431 s、峰值 RSS 128,262,144 B |

## 五、索引与覆盖缺口

`source_files` 共 4,889 行，按 `parse_status` 分组：

| 状态 | 数量 | 说明 |
| --- | ---: | --- |
| `parsed` | 2,405 | 仅 Go（1,310）与 Python（1,095）适配器实际解析 |
| `unsupported` | 2,338 | TypeScript、YAML、Markdown、JavaScript、JSON 及 unknown 等尚无解析适配器 |
| `excluded` | 146 | 87 个依赖/生成物、53 个二进制、6 个符号链接 |
| `oversized` / `invalid_encoding` | 0 | 无 |

敏感标记共 141 个文件，其中 `parsed` 70 个、`unsupported` 71 个；这些文件不返回原文，属于设计内覆盖缺口。另有 2 个非缓存文件大于 2 MiB（字体 `14,495,132 B`、DOCX `7,354,803 B`），均为二进制，未进入文本索引；没有源码文本文件超过索引上限。

**结论**：`source_import`、快照解压和索引任务均成功，但 `indexed_files=4,889` 不等于全部源码都已被语义解析。当前可复核的解析覆盖为 2,405/4,889（约 49.2%），其余主要是未支持语言和按策略排除项；这是后续 Gate 2 前必须记录的覆盖缺口，不是本次导入失败。

## 六、是否需要重新上传

不需要。原归档保留在验收环境命名卷 `/data/archives/b0e41631-20d0-494a-bde8-7cc2a5ad415a.zip`，已通过既有重试机制重新执行并成功；宿主机临时副本 `/tmp/p31b-real-waf.zip` 已删除。真实源码 ZIP 不在 Git 仓库中，也不提交到仓库。

## 七、证据

- `docs/acceptance/p31b_real_waf_zip_job_before.json`
- `docs/acceptance/p31b_real_waf_zip_retry.json`
- `docs/acceptance/p31b_real_waf_zip_job_after.json`
- `docs/acceptance/p31b_real_waf_zip_index_job.json`
- `docs/acceptance/p31b_real_waf_zip_index_status.json`
- `docs/acceptance/p31b_real_waf_zip_db_verify.log`
- `docs/acceptance/p31b_real_waf_zip_fix_regression.log`
- `docs/acceptance/p31b_real_waf_zip_pytest.log`
- `docs/acceptance/p31b_real_waf_zip_ruff.log`
- `docs/acceptance/p31b_real_waf_zip_build.log`
- `docs/acceptance/p31b_real_waf_zip_env_start.log`
- `docs/acceptance/p31b_real_waf_zip_index_headers.txt`
- `docs/acceptance/p31b_real_waf_zip_retry_headers.txt`
- `docs/acceptance/p31b_real_waf_zip_snapshot_before_index.json`
