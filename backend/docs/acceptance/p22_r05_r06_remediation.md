# P2.2 第二轮审核整改报告（R05–R06）

- 基线提交：`2cd39ec3e86557487e5822bac9e71042c7cba8db`
- 分支：`main`
- 范围：仅整改 R05–R06，不重开 P2.2，不进入 P2.3
- 原始日志目录：`docs/acceptance/`
- 结论：R05 的“至少 N 项”全集真实性、R06 的逐选项支持/否定合流已整改，
  定向、历史回归、全量、Ruff 和真实 PostgreSQL 专项均通过；
  真实授权模型、真实 WAF Word、真实雷池知识库仍未验收。

## 1. 整改结论

| 审核项 | 整改结果 | 关键验证 |
|---|---|---|
| R05 至少 N 项候选全集真实性 | 通过 | 只有原文出现明确全集限定表述才可 `complete_set=true`；记录依据、原文位置和不确定性；无可靠依据保持 false |
| R06 至少 N 项跨候选支持/否定冲突 | 通过 | 遍历全部已验证候选；支持与否定分别聚合；同选项冲突保持无法确认；结论不随候选顺序变化；否定证据可追溯 |
| 模型不得补造全集 | 通过 | `minimum_count` 不再进入模型 `_assist` 的待判定集合 |
| 证据回读失败/候选截断 | 通过 | 受影响选项降级为无法确认，并写入 `retrieval_issues`，不把不完整检索当成否定 |

关键词、字符二元组和相似度仍只用于召回与排序；直接支持、否定范围和至少 N 项
统计均由确定性规则给出。无法确定时保持“无法确认”。

## 2. R05：全集真实性

### 2.1 实现

- `equivalence.minimum_scope()` 不再把“有枚举选项 + 数量单位 + 无开放词”直接视为完整集合。
- 新增明确全集限定识别，当前覆盖以下原文表述及其等价写法：
  - “仅限以下选项/下列选项/如下选项”；
  - “以下三项中/从下列三项中”；
  - “以下为全部可选项/全部可选项如下”；
  - “三项中选两项/三选二”等明确 N 选 M 表述。
- 详情新增：
  - `complete_basis`：命中的全集限定类型；
  - `complete_marker`：命中的原文片段；
  - `complete_marker_start` / `complete_marker_end`：相对原始要求文本的位置；
  - `uncertainty`：无明确全集依据、开放枚举、选项不可枚举、缺数量单位、
    声明数量与解析选项数不一致、声明的 N 选 M 与实际 N 不一致等原因。
- 只要出现“包括但不限于/不限于/例如/比如/任意/等/或更多”等开放标记，
  即使同时出现全集字样，也不得标记完整集合。
- 原文声明“三项”或“三选二”而实际解析选项数不一致时，保持 `complete_set=false`。
- 仍允许：已确认支持数达到 N 时判“支持”，不要求先证明全集。
- 仍只在完整集合中最大可能支持数不足 N、且不支持的选项有有效否定证据时判“不支持”。
- `minimum_count` 不进入模型辅助判断，避免模型虚构全集范围。

### 2.2 反例覆盖

- 仅列举两项但未声明全集：`complete_set=false`，不判“不支持”。
- 明确“以下三项中选两项”：`complete_set=true`，并记录 `choose_n_of_m` 依据。
- 明确“从下列三项中”：`complete_set=true`，并记录原文位置。
- 明确“以下为全部可选项”：全部选项被否定且 N 项不足时判“不支持”，
  同时保留 `verified_contradiction` 引用。
- 有全集字样但同时出现“包括但不限于”：`complete_set=false`。
- 无明确全集依据但已确认支持数达到 N：仍判“支持”。
- 声明三项但只解析出两项：`complete_set=false`，记录数量不一致。
- 无数量单位、无选项或开放枚举：保持“无法确认”。

## 3. R06：逐选项支持/否定合流

### 3.1 实现

- `AtomicEvaluator._minimum()` 移除首个支持候选后的提前 `break`，对每个选项遍历
  检索返回的全部已验证候选。
- 同一选项分别聚合：
  - 直接支持候选；
  - 通过 `negative_assertion_bindings()`、`evidence_ids` 和
    `negative_scope_matches()` 校验的正式否定断言。
- 判定顺序：
  - 同一选项同时存在支持与否定：`无法确认`（`conflicting_support_and_negative`）；
  - 仅有支持：`支持`；
  - 仅有正式否定：`不支持`（`explicit_limitation`）；
  - 两者均无：`无法确认`（`no_match`）。
- 不再由候选排序决定结论；交换支持/否定候选顺序，结论保持一致。
- 每个选项的详情保留：
  - `support_candidate_ids` / `negative_candidate_ids`；
  - `support_evidence_ids` / `negative_evidence_ids` / `evidence_ids`；
  - 否定候选的 `verified_contradiction` 引文仍写入顶层 `citations`。
- 否定证据只使用已审核 `limitations.N` 断言绑定，并要求 evidence ID 在候选证据中存在；
  不是任意包含“不支持”字样的引文。
- `RetrievalResult.gaps` 非空或 `truncated=true` 时，受影响选项保持
  `无法确认`（`retrieval_incomplete`），并记录 `retrieval_issues`；
  不据不完整检索确认支持或否定。
- 不支持选项的否定证据引用同时保留在选项详情和 `citations`，不是只有文字理由。

### 3.2 反例覆盖

- 同一候选同时直接支持并明确否定同一选项：无法确认。
- 一个候选支持、另一个候选明确否定：无法确认。
- 支持候选排在否定候选之前或之后：结论一致。
- 全部候选均为支持：支持，且无否定证据引用。
- 完整全集内支持数不足 N 且有正式否定：不支持，并保留否定证据。
- 证据回读失败：受影响选项无法确认。
- 候选截断：受影响选项无法确认。
- 完整集合中仍有未知项时，不因其他选项否定而把未知项当成不支持。

## 4. 对既有断言的必要修正

R05 收紧后，旧测试中把“仅枚举选项、有数量单位、无开放词”当作完整集合的断言
不再成立。相关测试已改为带明确全集限定的原文，例如“仅限以下选项”或
“以下三项中选两项”；没有放宽生产校验，也没有降低 R01–R04 的门槛。

5 参数/15 参数集成用例和真实 PostgreSQL 专项的既有计数未改变。

## 5. 最终验证

### 5.1 P2.2 定向

    cd backend
    uv run pytest -q -vv -s --durations=10 \
      tests/unit/test_p22_equivalence.py tests/unit/test_p22_atomic.py \
      tests/unit/test_p22_logic.py tests/integration/test_p22_api.py \
      tests/integration/test_p22_model.py

结果：**73 passed, 1 warning in 3.69s**。
原始日志：`docs/acceptance/p22_r05_r06_targeted.log`。

### 5.2 P1.1～P2.1 回归

排除 P2.2 定向文件及 PostgreSQL 基础设施：

    uv run pytest -q \
      --ignore=tests/integration/test_p22_api.py \
      --ignore=tests/integration/test_p22_model.py \
      --ignore=tests/unit/test_p22_atomic.py \
      --ignore=tests/unit/test_p22_logic.py \
      --ignore=tests/unit/test_p22_equivalence.py \
      --ignore=tests/infrastructure

结果：**329 passed, 2 warnings in 74.54s**。
原始日志：`docs/acceptance/p22_r05_r06_p11_p21_regression.log`。

### 5.3 全量

    cd backend
    uv run pytest -q

结果：**402 passed, 31 skipped, 2 warnings in 59.11s**。
跳过项为需显式 PostgreSQL 环境的测试和既有外部服务测试，不是失败。
原始日志：`docs/acceptance/p22_r05_r06_full_pytest.log`。

### 5.4 真实 PostgreSQL 16.9

    cd backend
    DATABASE_URL='postgresql+psycopg://knowledge:***@127.0.0.1:55439/knowledge' \
    P22_RUN_POSTGRES=1 uv run pytest -q -vv -s tests/infrastructure/test_p22_postgres.py

结果：**1 passed, 1 warning in 2.13s**；任务内部 `elapsed_ms`：
5 参数 **71 ms**、15 参数 **161 ms**。该数据是本机测试采样，不是生产压测或真实模型时延。
原始日志：`docs/acceptance/p22_r05_r06_postgres.log`。

### 5.5 静态检查

    cd backend
    uv run ruff check .
    uv run ruff format --check .

结果：All checks passed；**184 files already formatted**。
原始日志：`docs/acceptance/p22_r05_r06_ruff.log`。

## 6. 未验收项

以下项目仍未被真实环境验收，不能把合成结果当作通过：

- 真实企业授权模型端点与真实模型语义等价质量；当前仍无真实模型请求。
- 真实《WAF 招标技术参数表.docx》；Word 15 参数/45 原子来自合成 fixture。
- 真实已发布雷池 WAF 知识库；所有“支持/不支持”只针对合成测试知识。
- 生产并发容量、真实时延、Token 成本和误报率。

## 7. 阶段状态

R05–R06 整改完成，等待人工复审；未进入 P2.3。
