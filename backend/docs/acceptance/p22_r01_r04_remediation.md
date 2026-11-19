# P2.2 第一轮审核整改报告（R01–R04）

- 基线提交：f8173bc08fe53cd5315a27506ffe1d2fb67399b5
- 分支：main
- 范围：仅整改 R01–R04，不重开 P2.2，不进入 P2.3
- 原始日志目录：docs/acceptance/
- 结论：R01–R04 的确定性门槛、模型负向绑定和至少 N 项集合校验已整改并通过回归；
  真实授权模型、真实 WAF Word、真实雷池知识库仍未验收。

## 1. 整改结论

| 审核项 | 整改结果 | 关键验证 |
|---|---|---|
| R01 技术支持可靠性 | 通过 | 动作/部署/自动化/范围差集、对象与条件覆盖；检测≠检测并阻断、监测≠自动响应、单机≠集群高可用 |
| R02 量化指标比较 | 通过 | 指标名、单位、运算符、适用范围共同绑定；Gbps/Mbps、秒/ms 换算；默认配置值不作为产品上限 |
| R03 模型负向判断绑定 | 通过 | 仅接受已审核 limitations.N 证据；校验 evidence_id、引文和否定范围；支持证据与否定冲突不得由模型裁决 |
| R04 至少 N 项完整集合 | 通过 | 明确数量单位、开放集合和候选全集；已支持数≥N 才支持，仅完整集合最大可能数<N 才不支持 |

关键词、字符二元组和相似度仍只用于召回与排序；直接支持改由受控文本门槛核验，
无法确定时保持“无法确认”。数值低于要求只记为上下文矛盾，不自动推出“不支持”。

## 2. R01：技术支持可靠性

### 2.1 实现

- 增加受控技术动作组：检测、识别、阻断、响应、监测、解析、导出、管理、审计、
  认证、加密、日志、部署、隔离、备份/恢复。
- 增加部署、自动化、范围限定组：单机、双机、热备/主备、集群、高可用；
  自动/联动、手动/人工；全部/任意/全量、部分/指定、仅/只。
- 以 direct_support() 替代词面直接支持路径，必须同时满足：
  - 要求的动作集合是候选动作集合的子集；
  - 部署、自动化、范围限定不缺失；
  - 英文/数字对象、核心术语覆盖率和条件覆盖率达标；
  - 要求含性能语义时，候选必须明确覆盖性能范围。
- 阈值要求先用 quantitative_scope() 去掉数值和比较符，再核验指标对象，避免
  数值相等掩盖动作不一致。
- 候选的已审核 conditions 只用于判断要求条件是否被覆盖；条件文本不得补充能力
  正文中缺失的技术动作。
- 删除旧 strong_lexical_match() 直接支持路径；terms()/coverage() 仅服务检索。

### 2.2 反例覆盖

- SQL 注入“检测”不能支持“检测并阻断”。
- 恶意文件“识别”不能支持“识别并处置”。
- 异常流量“监测”不能支持“监测与自动响应”。
- “单机部署”不能支持“集群高可用部署”，反向同样不成立。
- 正确直接支持仍保持支持，包括 HTTP/1.1/HTTP/2 协议解析、IPv6 与 IPv4 双栈解析。
- 源码证据和文档证据任一充分均可形成支持；无证据仍为“无法确认”。

## 3. R02：量化指标比较

### 3.1 实现

- metric_evaluation() 把阈值与候选数值绑定到指标名称、单位、运算符和适用条件。
- 单位族覆盖速率（Gbps/Mbps/Kbps/bps）、时延（ms/毫秒/秒/分钟/小时）、
  计数（个/条/路/台/次）、QPS/TPS、百分比；支持同族单位换算。
- 候选 _claims 为每个数值记录指标名、单位、运算符、是否上限声明、是否默认/测试
  环境配置，禁止取候选文本最大数字直接比较。
- 版本号、并发数、吞吐量、时延数字按指标名称和单位过滤，不跨指标混用。
- “默认/建议/典型/出厂/测试环境/单机配置”数值不作为已确认产品上限；
  “最大/最高/可达/上限/峰值/不超过”才作为上限声明。
- 缺单位、多个同指标数值无法确定上限、只有默认配置值、指标名或运算符无法绑定、
  适用范围不清时返回 unknown。
- 数值低于要求返回 not_satisfied，但只写入 numeric_contradiction 上下文；
  没有正式否定证据时不据此判“不支持”。

### 3.2 反例覆盖

- 同一候选包含 HTTP 版本、并发数和吞吐量数字时，只用与指标名匹配的吞吐量。
- 1 Gbps 与 1200 Mbps 通过换算判定满足；100 ms 与 0.05 秒通过换算判定满足。
- 明确“最大并发 20000 个”可作为产品上限；仅有“默认最大并发 1000 个”时无法确认。
- 默认标记位于数值之后（如“20000 个（默认配置）”）仍按默认配置处理，不作为产品上限。
- 最大并发 5000 个低于 10000 个时仍为“无法确认”，不是“不支持”。
- 缺单位、两个无上限或两个互相矛盾的“最大”吞吐量数值均无法确认。
- 计数单位不一致（如“个”与“条”）不自动换算，按指标无法绑定处理。

## 4. R03：模型负向判断绑定

### 4.1 实现

- negative_assertion_bindings() 只从已审核且 semantic_decision=supported 的
  limitations.N 生成否定声明，并绑定该断言的 evidence_ids；单纯引文出现
  “不支持/不提供/未实现”不构成否定证据。
- negative_scope_matches() 校验否定句与要求具有相同技术动作、对象、部署/自动化/
  适用范围；性能要求还必须出现性能限定。对象不同（IPv4/IPv6）、动作不同
  （检测/检测并阻断）、部署形态不同（单机/集群）均不匹配。
- 模型 contradicts 校验：
  - rationale 必须非空；
  - 该原子已有 verified_support 时拒绝，冲突不得由模型裁决；
  - evidence_ids 必须属于该候选已审核否定断言；
  - anchor 引文必须回读到该否定断言；
  - 否定范围必须通过 negative_scope_matches()。
- 拒绝原因分别为 contradiction_conflicts_with_support、
  contradiction_evidence_not_audited、contradiction_scope_or_binding_mismatch。

### 4.2 反例覆盖

- 否定引文存在但否定 IPv4、要求 IPv6：无法确认。
- 要求集群高可用、否定的是单机部署：无法确认。
- 模型引用未审核否定 evidence_id：拒绝。
- 模型引用“不支持单机部署”来解释集群要求：拒绝范围不匹配。
- 已审核限制为“无法提供 IPv6 协议解析”时，同一对象的要求可判“不支持”。
- 已有直接支持证据时模型输出 contradicts：拒绝。
- 有效且范围一致的已审核否定断言：接受。
- 没有正式否定证据：仍为“无法确认”。

## 5. R04：至少 N 项完整集合

### 5.1 实现

- minimum_scope() 明确数量单位、开放集合标记、选项数量和完整集合状态。
- 只有满足以下条件才视为完整集合：有“至少/不少于/不低于/不小于 N 项/种/个/条/类/款”、
  有可枚举选项、选项不超过 20 个、文本不含“包括但不限于/不限于/例如/比如/任意/任选/等”
  等开放标记；等保、等级保护等专有词不按开放标记处理。
- 支持：已确认支持数 ≥ N。
- 不支持：仅当完整集合可证明，且 已支持数 + 未知数 < N，即最多可能支持数不足 N。
- 候选全集未知、开放枚举、缺数量单位、选项无法枚举时保持“无法确认”；
  不把缺少证据或未找到的选项当作产品不支持。
- 详情记录 supported、unsupported、unknown、max_possible_supported、
  complete_set、open_markers 和 count_unit，便于复核统计口径。

### 5.2 反例覆盖

- 完整集合 3 项，已确认 2 项支持，要求至少 2 项：支持。
- 完整集合 2 项，1 项支持、1 项明确否定，要求至少 2 项：不支持。
- 完整集合 3 项，1 项支持、1 项否定、1 项未知：仍可能达到 2 项，无法确认。
- 单个选项同时存在直接支持和已审核否定证据时，保持无法确认，不由统计口径裁决。
- 只有“至少 2 项安全功能”没有候选全集：无法确认。
- “包括但不限于”开放枚举：无法确认。
- 缺少数量单位“至少 N 项”：无法确认。
- “不少于 2 项”与“至少 2 项”按同一统计口径处理；等保不是开放枚举。

## 6. 对既有断言的必要修正

判定收紧后，两处旧测试期望按新语义调整，未放宽生产校验：

1. 5 参数集成用例中，合成知识只有“多租户策略”，没有“隔离”技术动作。
   旧词面匹配把它算作支持；新门槛保留“无法确认”，因此
   test_single_parameter_failure_is_isolated_and_fail_closed 的非失败参数支持数
   由 4 改为 3，并在测试内注明原因。
2. 并发阈值用例补上数量单位“个”。R02 要求数值绑定单位，缺单位时按无法确认处理，
   不再用裸数字猜测。

真实 PostgreSQL 专项新增否定判断后，独立合成快照分类由 2 个变为 3 个，
计数断言同步为 3；未改变生产表结构，也没有新增迁移。

## 7. 最终验证

### 7.1 P2.2 定向

    cd backend
    uv run pytest -q -s --durations=10 \
      tests/unit/test_p22_logic.py tests/unit/test_p22_atomic.py \
      tests/unit/test_p22_equivalence.py tests/integration/test_p22_api.py \
      tests/integration/test_p22_model.py

结果：63 passed, 1 warning in 3.22s。
原始日志：docs/acceptance/p22_r01_r04_targeted.log。

### 7.2 P1.1～P2.1 回归

排除 P2.2 定向文件及 PostgreSQL 基础设施：

    uv run pytest -q \
      --ignore=tests/integration/test_p22_api.py \
      --ignore=tests/integration/test_p22_model.py \
      --ignore=tests/unit/test_p22_atomic.py \
      --ignore=tests/unit/test_p22_logic.py \
      --ignore=tests/unit/test_p22_equivalence.py \
      --ignore=tests/infrastructure

结果：329 passed, 2 warnings in 57.33s。
原始日志：docs/acceptance/p22_r01_r04_p11_p21_regression.log。

### 7.3 全量

    cd backend
    uv run pytest -q

结果：392 passed, 31 skipped, 2 warnings in 71.49s。
跳过项为需显式 PostgreSQL 环境的测试和既有外部服务测试，不是失败。
原始日志：docs/acceptance/p22_r01_r04_full_pytest.log。

### 7.4 真实 PostgreSQL 16.9

    cd backend
    DATABASE_URL='postgresql+psycopg://knowledge:***@127.0.0.1:55439/knowledge' \
    P22_RUN_POSTGRES=1 uv run pytest -q -vv -s tests/infrastructure/test_p22_postgres.py

结果：1 passed, 1 warning in 2.21s；任务内部 elapsed_ms：5 参数 75 ms、
15 参数 208 ms。该数据是本机测试采样，不是生产压测或真实模型时延。

本次专项新增：

- 5 项参数中至少存在直接支持结论，验证修正后的支持路径未被全部降级。
- 发布“支持 IPv4 协议解析 / 不支持 IPv6 协议解析”的合成知识后，要求“支持 IPv6
  协议解析”得到明确“不支持”，并产生 verified_contradiction 证据。

原始日志：docs/acceptance/p22_r01_r04_postgres.log。

### 7.5 静态检查

    cd backend
    uv run ruff check .
    uv run ruff format --check .

结果：All checks passed；184 files already formatted。
原始日志：docs/acceptance/p22_r01_r04_ruff.log。

## 8. 未验收项

以下项目仍未被真实环境验收，不能把合成结果当作通过：

- 真实企业授权模型端点与真实模型语义等价质量；当前仍无真实模型请求。
- 真实《WAF 招标技术参数表.docx》；Word 15 参数/45 原子来自合成 fixture。
- 真实已发布雷池 WAF 知识库；所有“支持/不支持”只针对合成测试知识。
- 生产并发容量、真实时延、Token 成本和误报率。

## 9. 阶段状态

R01–R04 整改完成，等待人工审核；未进入 P2.3。
