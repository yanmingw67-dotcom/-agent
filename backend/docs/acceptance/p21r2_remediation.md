# P2.1 第二轮审核补充整改说明（R05/R06/开放枚举）

## 范围

本轮只做增量修复，未重构身份体系、模型调用框架或人工修正流程：

- R05：修正表格参数子要求的 `source_span` 坐标，使其严格对应所属参数的 `original_text`。
- R06：技术功能与证明材料混写时，只过滤证明材料子句，保留技术条件。
- 开放枚举：区分“全部必要”“示例”“无法确定”三种语义，无法确定时进入人工确认。

## R05 根因与修复

根因：`_table_candidate()` 先用技术正文生成子要求，再把“序号 + 功能名称”拼入参数原文；子要求锚点仍停留在技术正文坐标系。续行合并又对整段原文重新执行 `build_requirements()`。

修复：

1. `_candidate_from_text()` 支持显式的 `requirement_body` / `requirement_offset`，并在完整参数原文上逐字校验
   `anchor[offset:offset+len(body)] == body`；校验失败时退回整体原文解析，不虚构偏移。
2. 表格候选先构造 `序号 + 功能名称\n技术正文`，再以技术正文的偏移生成子要求；参数 ID、顶层边界和原文序号保持不变。
3. 续行合并改为 `_merge_continuation()`：保留已校验锚点，只对新增候选树平移一次，并追加到逻辑树；不再重新解析整段原文。
4. 过滤证明材料命中续行时，保留原参数及其定位；新增子要求仍使用同一原文坐标系。

验证：新增测试对 Word 普通表格、Word 合并单元格/续行、Excel 表格和 PDF 表格逐条断言
`original_text[start:end] == requirement.original_text`；覆盖参数原文前缀、技术正文带空格和跨行合并场景。

## R06 根因与修复

根因：原逻辑把“技术功能 + 证明材料”整行/整单元格当作证明材料，导致技术条件被整段过滤。

修复：

1. 新增 `_strip_proof()`，只删除明确的证明材料子句（提供/提交/出具/附上/加盖/附，后接检测报告、测试报告、证明材料、截图、证书、承诺函、授权函等）。
2. `_EXPORT_FUNCTION` / `PROOF_EXPORT` 保护“支持导出 PDF 日志/报告”“提供检测报告导出功能”等产品功能，不把导出能力误判为证明材料。
3. 文本和表格行均先剥离证明材料，再判断是否仍有技术动作；技术功能保留为候选，纯证明材料行不生成参数。
4. 每个被剥离片段写入 `filtered` 覆盖记录，包含 `proof_material` 原因、原区域定位和 `filtered_span`，不无痕丢弃。
5. 剥离后仍残留不确定材料词或缺少技术动作时，标记参数 `review_required=True`；不做不可逆删除。

验证：新增测试覆盖技术功能 + 截图、技术功能 + 检测报告、单独检测报告、产品日志/报表导出、多编号复合参数中的证明材料子句、Word 表格技术正文内部混写证明材料。所有保留参数仍逐字校验锚点。

## 开放枚举语义

- 明确出现“全部/所有/均/逐项/各项/必须/须……具备/满足”等语境时，列举项标记为 `is_mandatory=True`、`mandatory_status="required"`。
- 出现“例如/示例/举例/譬如/等”等示例语境时，保留 `example_relation=True`、`open_enumeration_mode="example"`，不标记为逐项必须。
- 没有足够限定词时，保留 `open_enumeration_mode="uncertain"`、`mandatory_status="uncertain"`，并将参数标记 `review_required=True`；不把“包括但不限于”直接当作可选项。
- 纯限定词（如“且均须具备”）不生成额外子要求；`AND_OPEN` / `OR_OPEN` 的开放关系保留。

验证：新增开放枚举三种语义测试，并保留原有“不自动逐项必须”的回归断言。

## 本轮执行结果

原始日志均在 `backend/docs/acceptance/`：

- `p21r2_targeted.log`：P2.1 单元与集成定向测试，50 passed。
- `p21r2_full_pytest.log`：全量 pytest，329 passed、30 skipped。
- `p21r2_p14_p16_regression.log`：P1.4～P1.6 定向回归，118 passed。
- `p21r2_postgres.log`：临时 PostgreSQL 16.9 专用库测试，1 passed。
- `p21r2_migration.log`：0009 升级、回退 0008、再次升级成功。
- `p21r2_ruff_check.log`、`p21r2_ruff_format.log`：静态检查通过。
- `p21r2_real_file_status.log`：真实《WAF招标技术参数表.docx》仍未找到，真实文件验收未通过。

## 明确未验收边界

1. 真实《WAF招标技术参数表.docx》仍不可用；本轮新增表格测试均为合成样例，不能替代真实文件验收。
2. PDF 表格单元测试使用 ASCII 表格并注入角色映射，验证的是 PDF 表格解析路径和锚点坐标，不等同于真实中文 PDF 验收。
3. 未执行真实授权模型提取质量验收；FunctionModel 仅验证管线。
4. 扫描 PDF OCR 仍未实现。
5. 新语义字段随参数的 `atomic_requirements` JSON/API 返回；现有 `tender_atomic_requirements` 表未新增迁移列，数据库行仍以 `is_mandatory`、`ambiguity` 和参数的 `review_required` 表达核心状态。

本轮完成后暂停，不进入 P2.2。
