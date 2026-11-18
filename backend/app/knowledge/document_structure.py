"""P1.6 文档结构恢复与内容分类。

复用 P1.2 解析器产生的 DocumentSegment 与 locator，不新建 Office/PDF 解析器：
- DOCX：段落顺序、标题章节、表格行、列标题、合并单元格去重与继承；
- XLSX：Sheet、表头行、列角色、跨行合并区域、重复表头；
- PDF：页码、表格行列、page_text 与表格单元格去重、OCR/空页缺口；
- MD/TXT：标题层级、项目符号、参数段落。

结构判断优先于关键词：列角色和章节先决定信息用途，再以语义模式分类。
所有单元保留 document_id + segment_ids + locator，供证据登记精确回指。
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.index_models import Document, DocumentSegment

ALLOWED = {
    "current_capability",
    "technical_implementation",
    "support_scope",
    "deployment",
    "configuration",
    "performance_metric",
    "limitation",
    "exclusion",
}
NON_PRODUCT = {
    "proof_material",
    "competitor",
    "commercial",
    "marketing",
    "future_plan",
    "scenario",
    "reference",
    "unclassified",
    "table_header",
}

FUTURE = re.compile(
    r"计划(?:支持|提供|实现|中)|规划|后续(?:版本)?|未来(?:版本)?|将(?:支持|提供)|拟(?:支持|提供)"
    r"|可开发|建议支持|待开发|尚未支持|roadmap|future release|planned",
    re.I,
)
PROOF = re.compile(
    r"证明材料|截图|检测报告|测试报告|第三方报告|证书|承诺函|授权函|加盖公章|须提供|需提供"
    r"|投标人应|供应商应|提供.{0,8}(?:证明|报告|证书)",
    re.I,
)
PROOF_EXPORT = re.compile(r"支持.{0,10}(?:导出|生成|管理|查看).{0,8}(?:报告|截图|日志)", re.I)
COMPETITOR = re.compile(r"友商|竞品|控标|对比品牌|某品牌|竞对|其他厂商", re.I)
COMMERCIAL = re.compile(
    r"商务|售后服务|质保|报价|折扣|培训服务|服务承诺|响应时间.{0,6}小时|SLA|付款|交付周期", re.I
)
MARKETING = re.compile(r"业界领先|行业第一|卓越|完美|强大|颠覆|最优|顶级|极致|遥遥领先", re.I)
EXCLUSION = re.compile(r"不支持|不提供|不具备|不涉及|无法|禁止|排除|\bnot supported\b", re.I)
LIMITATION = re.compile(
    r"仅(?:支持|限|适用|能)|限制|约束|除外|受限于|上限为|最小为|最大为|不得|不可|不支持", re.I
)
DEPLOY = re.compile(
    r"部署|旁路|透明桥|透明代理|反向代理|镜像|串联|桥接|双机热备|高可用|HA"
    r"|deploy(?:ment)?|transparent proxy|reverse proxy|inline|bypass|mirror|high availability",
    re.I,
)
PERF = re.compile(
    r"吞吐|并发|时延|延迟|QPS|TPS|连接数|新建.{0,3}秒|Gbps|Mbps|容量|每秒"
    r"|throughput|concurren|latency|connections?",
    re.I,
)
CONFIG = re.compile(r"配置|参数|开关|策略|规则|白名单|黑名单", re.I)
TECH = re.compile(r"基于|采用|通过.{0,24}实现|架构|模块|算法|机制|引擎|内核", re.I)
CAPABILITY = re.compile(
    r"支持|具备|提供|可实现|能够|允许|集成|兼容|覆盖|识别|防护|检测|阻断|记录|同步|隔离|加密|审计"
    r"|\bsupports?\b|\bsupported\b|\bprovides?\b|\ballows?\b|\benables?\b",
    re.I,
)
LEGAL = re.compile(r"目录|声明|免责|版权|联系方式|更新日志|版本历史|下载|商标|保密", re.I)
FUTURE_SECTION = re.compile(r"规划|路线图|roadmap|未来|后续计划", re.I)
HEADER_WORDS = re.compile(
    r"功能|能力|参数|指标|技术|支持|说明|名称|要求|配置|规格|备注|分类|类别|模块|范围|条件", re.I
)

ROLE_RULES = [
    ("proof_material", re.compile(r"证明材料|截图|检测报告|测试报告|证书|承诺函|证明文件", re.I)),
    ("competitor", re.compile(r"友商|竞品|控标|对比|其他厂商", re.I)),
    ("commercial", re.compile(r"商务|服务|售后|价格|报价|授权|质保", re.I)),
    ("future", re.compile(r"规划|计划|后续|roadmap|未来|建议", re.I)),
    ("scenario", re.compile(r"场景|方案|话术|优势|价值|卖点", re.I)),
    ("category", re.compile(r"分类|类别|大类|模块|功能分类|分组", re.I)),
    ("performance", re.compile(r"性能|吞吐|并发|时延|延迟|容量|数量|QPS|TPS|指标", re.I)),
    ("limitation", re.compile(r"限制|约束|备注|说明|除外|不支持", re.I)),
    ("condition", re.compile(r"条件|前提|配置|要求", re.I)),
    ("scope", re.compile(r"范围|形态|版本|适用", re.I)),
    ("capability", re.compile(r"功能|能力|参数|技术|支持|规格|描述|项目|条款", re.I)),
]
HEADER_ROLE_SET = {name for name, _ in ROLE_RULES}


def column_role(header: str | None) -> str:
    text = (header or "").strip()
    for role, pattern in ROLE_RULES:
        if pattern.search(text):
            return role
    return "unknown" if not text else "capability"


def split_coord(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", value.strip())
    if not match:
        return None
    letters, row = match.groups()
    column = 0
    for char in letters.upper():
        column = column * 26 + (ord(char) - 64)
    return int(row), column


def range_cells(value: str) -> list[tuple[int, int]]:
    match = re.fullmatch(r"([A-Za-z]+\d+):([A-Za-z]+\d+)", value.strip())
    if not match:
        point = split_coord(value)
        return [point] if point else []
    start, end = split_coord(match.group(1)), split_coord(match.group(2))
    if not start or not end:
        return []
    return [
        (row, column)
        for row in range(min(start[0], end[0]), max(start[0], end[0]) + 1)
        for column in range(min(start[1], end[1]), max(start[1], end[1]) + 1)
    ]


@dataclass
class Unit:
    key: str
    document_id: str
    filename: str
    segment_ids: list[str]
    locator: dict
    text: str
    kind: str
    section: str = ""
    table: int | None = None
    row: int | None = None
    columns: list[int] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    merged_context: dict = field(default_factory=dict)
    sheet: str | None = None
    classification: str = "unclassified"
    reasons: list[str] = field(default_factory=list)
    eligible: bool = False
    duplicate: bool = False
    ocr_required: bool = False

    def to_dict(self):
        return {
            "key": self.key,
            "document_id": self.document_id,
            "filename": self.filename,
            "segment_ids": self.segment_ids,
            "locator": self.locator,
            "text": self.text,
            "kind": self.kind,
            "section": self.section,
            "table": self.table,
            "row": self.row,
            "columns": self.columns,
            "headers": self.headers,
            "merged_context": self.merged_context,
            "sheet": self.sheet,
            "classification": self.classification,
            "classification_reasons": self.reasons,
            "eligible": self.eligible,
            "duplicate": self.duplicate,
            "ocr_required": self.ocr_required,
        }


def classify(text: str, *, kind: str = "paragraph", section: str = "", header_role: str = ""):
    """结构优先分类；返回 (classification, reasons)。"""
    reasons = []
    value = text.strip()
    if header_role in {"proof_material", "competitor", "commercial", "future", "scenario"}:
        return {
            "proof_material": "proof_material",
            "competitor": "competitor",
            "commercial": "commercial",
            "future": "future_plan",
            "scenario": "scenario",
        }[header_role], [f"column_role:{header_role}"]
    if kind == "table_header":
        return "table_header", ["table_header_not_capability"]
    if section and FUTURE_SECTION.search(section):
        return "future_plan", ["section_role:future_plan"]
    if section and LEGAL.search(section):
        return "reference", ["section_role:legal_or_reference"]
    if PROOF.search(value) and not PROOF_EXPORT.search(value):
        return "proof_material", ["pattern:proof_material"]
    if COMPETITOR.search(value):
        return "competitor", ["pattern:competitor"]
    if MARKETING.search(value):
        return "marketing", ["pattern:marketing"]
    if COMMERCIAL.search(value):
        return "commercial", ["pattern:commercial"]
    if FUTURE.search(value):
        return "future_plan", ["pattern:future_plan"]
    if EXCLUSION.search(value):
        remainder = EXCLUSION.sub(" ", value)
        if not re.search(r"支持|具备|提供|可实现|能够|允许|集成|兼容|可以", remainder):
            return "exclusion", ["pattern:exclusion"]
    if LIMITATION.search(value):
        return "limitation", ["pattern:limitation"]
    if DEPLOY.search(value):
        return "deployment", ["pattern:deployment"]
    if PERF.search(value):
        return "performance_metric", ["pattern:performance"]
    if CAPABILITY.search(value):
        if CONFIG.search(value) and not re.search(r"支持|具备|提供|能够|允许|能够", value):
            return "configuration", ["pattern:configuration"]
        return "current_capability", ["pattern:product_capability"]
    if TECH.search(value):
        return "technical_implementation", ["pattern:technical_mechanism"]
    if CONFIG.search(value):
        return "configuration", ["pattern:configuration"]
    reasons.append("no_product_capability_signal")
    return "unclassified", reasons


def dedupe_texts(texts):
    result = []
    for text in texts:
        if text and text not in result:
            result.append(text)
    return result


def header_row(rows: dict[int, dict[int, str]], first: int) -> int:
    for row in sorted(rows):
        texts = dedupe_texts(rows[row].values())
        if len(texts) >= 2 and any(HEADER_WORDS.search(t) for t in texts):
            return row
        if row >= first + 2:
            break
    return first


def build_docx_units(document, segments):
    units = []
    section = ""
    tables: dict[int, dict[int, dict[int, tuple]]] = {}
    paragraphs = []
    for segment in segments:
        locator = segment.locator or {}
        if "table" in locator:
            tables.setdefault(locator["table"], {}).setdefault(locator.get("row", 0), {})[
                locator.get("column", 0)
            ] = (segment, segment.text)
        else:
            paragraphs.append(segment)
    for segment in paragraphs:
        locator = segment.locator or {}
        section = locator.get("section") or section
        kind = (
            "heading"
            if segment.text.strip() == section.strip() and len(section) <= 120
            else "paragraph"
        )
        if kind == "heading":
            classification, reasons = "reference", ["heading_not_capability"]
        else:
            classification, reasons = classify(segment.text, kind=kind, section=section)
        units.append(
            Unit(
                key=f"docx:p:{document.id}:{locator.get('paragraph', 0)}",
                document_id=document.id,
                filename=document.filename,
                segment_ids=[segment.id],
                locator=dict(locator),
                text=segment.text,
                kind=kind,
                section=section,
                classification=classification,
                reasons=reasons,
                eligible=classification in ALLOWED and kind != "heading",
            )
        )
    for table_index in sorted(tables):
        rows = tables[table_index]
        if not rows:
            continue
        first = min(rows)
        head_row = header_row(
            {r: {c: t for c, (_, t) in cols.items()} for r, cols in rows.items()}, first
        )
        heads = {c: text for c, (_, text) in rows.get(head_row, {}).items()}
        roles = {c: column_role(heads.get(c, "")) for c in heads}
        for row in sorted(rows):
            cells = rows[row]
            texts = dedupe_texts(t for _, t in (cells[c] for c in sorted(cells)))
            row_section = next(iter(cells.values()))[0].locator.get("section", section)
            locator = {
                "table": table_index,
                "row": row,
                "columns": sorted(cells),
                "section": row_section,
                "headers": {str(c): heads.get(c, "") for c in sorted(heads)},
            }
            if row == head_row:
                classification, reasons = classify(" ".join(texts), kind="table_header")
                unit_kind = "table_header"
                body_text = "；".join(texts)
                segment_ids = [cells[c][0].id for c in sorted(cells)]
            else:
                capability_cells = []
                context = []
                for column in sorted(cells):
                    segment, text = cells[column]
                    role = roles.get(column, "unknown")
                    if role in {
                        "proof_material",
                        "competitor",
                        "commercial",
                        "future",
                        "scenario",
                        "category",
                    }:
                        context.append((role, segment.id))
                        continue
                    if text.strip():
                        capability_cells.append((column, segment, text))
                if not capability_cells:
                    continue
                body_text = "；".join(
                    f"{heads.get(c, '')}: {text}" if heads.get(c) else text
                    for c, _, text in capability_cells
                )
                classification, reasons = classify(body_text, kind="table_row", section=row_section)
                for role, _ in context:
                    if role != "category":
                        reasons.append(f"context_column:{role}")
                unit_kind = "table_row"
                segment_ids = [s.id for _, s, _ in capability_cells] + [eid for _, eid in context]
            units.append(
                Unit(
                    key=f"docx:t:{document.id}:{table_index}:{row}",
                    document_id=document.id,
                    filename=document.filename,
                    segment_ids=segment_ids,
                    locator=locator,
                    text=body_text,
                    kind=unit_kind,
                    section=row_section,
                    table=table_index,
                    row=row,
                    columns=sorted(cells),
                    headers={str(c): heads.get(c, "") for c in sorted(heads)},
                    classification=classification,
                    reasons=reasons,
                    eligible=unit_kind == "table_row" and classification in ALLOWED,
                )
            )
    return units


def build_xlsx_units(document, segments):
    sheets: dict[str, dict[tuple[int, int], tuple]] = {}
    for segment in segments:
        locator = segment.locator or {}
        point = split_coord(locator.get("cell", ""))
        if not point:
            continue
        sheets.setdefault(locator.get("sheet", ""), {})[point] = (segment, segment.text)
    units = []
    for sheet, cells in sheets.items():
        rows = sorted({row for row, _ in cells})
        cols = sorted({column for _, column in cells})
        head_row = None
        for row in rows:
            values = [cells[(row, c)][1] for c in cols if (row, c) in cells]
            if len(values) >= 2 and any(HEADER_WORDS.search(v) for v in values):
                head_row = row
                break
        if head_row is None:
            head_row = rows[0] if rows else 1
        heads = {c: cells[(head_row, c)][1] for c in cols if (head_row, c) in cells}
        roles = {c: column_role(heads.get(c, "")) for c in cols}
        # 合并区域：仅把分类/范围/条件类锚点向下继承，不继承能力正文。
        inherited: dict[int, dict[int, str]] = {}
        for (row, column), (segment, text) in cells.items():
            for ref in (segment.locator or {}).get("merged_ranges", []) or []:
                covered = range_cells(ref)
                if not covered or covered[0] != (row, column):
                    continue
                role = roles.get(column, "unknown")
                if role not in {"category", "scope", "condition", "limitation"}:
                    continue
                for target_row, target_column in covered[1:]:
                    if target_column == column:
                        inherited.setdefault(target_row, {})[column] = text
        for row in rows:
            if row <= head_row:
                continue
            capability_cells = []
            context = {}
            related = []
            for column in cols:
                if (row, column) not in cells:
                    continue
                segment, text = cells[(row, column)]
                role = roles.get(column, "unknown")
                if role in {
                    "proof_material",
                    "competitor",
                    "commercial",
                    "future",
                    "scenario",
                    "category",
                    "limitation",
                    "condition",
                    "scope",
                }:
                    context[heads.get(column, str(column))] = text
                    related.append(segment.id)
                    continue
                if role in {"capability", "performance", "unknown"} and text.strip():
                    capability_cells.append((column, segment, text))
            if not capability_cells:
                continue
            combined = "；".join(
                f"{heads.get(c, '')}: {text}" if heads.get(c) else text
                for c, _, text in capability_cells
            )
            classification, reasons = classify(combined, kind="table_row", section=sheet)
            unit = Unit(
                key=f"xlsx:{document.id}:{sheet}:{row}",
                document_id=document.id,
                filename=document.filename,
                segment_ids=[s.id for _, s, _ in capability_cells]
                + related
                + [cells[(row, c)][0].id for c in inherited.get(row, {}) if (row, c) in cells],
                locator={
                    "sheet": sheet,
                    "row": row,
                    "range": f"{sheet}!{row}:{row}",
                    "cells": [
                        cells[(row, c)][0].locator.get("cell") for c, _, _ in capability_cells
                    ],
                    "headers": {str(c): heads.get(c, "") for c in cols},
                    "merged_context": {str(c): v for c, v in inherited.get(row, {}).items()},
                },
                text=combined,
                kind="table_row",
                section=sheet,
                columns=[c for c, _, _ in capability_cells],
                headers={str(c): heads.get(c, "") for c in cols},
                merged_context={str(c): v for c, v in inherited.get(row, {}).items()},
                sheet=sheet,
                classification=classification,
                reasons=reasons,
                eligible=classification in ALLOWED,
            )
            units.append(unit)
    return units


def build_pdf_units(document, segments):
    units = []
    by_page: dict[int, list] = {}
    for segment in segments:
        by_page.setdefault((segment.locator or {}).get("page", 0), []).append(segment)
    for page in sorted(by_page):
        page_segments = by_page[page]
        table_cells = {}
        page_texts = []
        for segment in page_segments:
            locator = segment.locator or {}
            if "table" in locator:
                table_cells.setdefault(locator["table"], {}).setdefault(locator.get("row", 0), {})[
                    locator.get("column", 0)
                ] = (segment, segment.text)
            else:
                page_texts.append(segment)
        cell_texts = {
            text.strip()
            for table in table_cells.values()
            for row in table.values()
            for _, text in row.values()
        }
        for segment in page_texts:
            remainder = segment.text
            for text in sorted(cell_texts, key=len, reverse=True):
                if text and text in remainder:
                    remainder = remainder.replace(text, " ")
            duplicate = bool(table_cells) and len(remainder.strip()) < 20
            classification, reasons = classify(
                remainder if remainder.strip() else segment.text, kind="page_text"
            )
            if duplicate:
                reasons = reasons + ["dual_extraction_suppressed_table_cells"]
            units.append(
                Unit(
                    key=f"pdf:p:{document.id}:{page}",
                    document_id=document.id,
                    filename=document.filename,
                    segment_ids=[segment.id],
                    locator=dict(segment.locator or {}),
                    text=segment.text if not remainder.strip() else remainder,
                    kind="page_text",
                    classification=classification,
                    reasons=reasons,
                    eligible=classification in ALLOWED and not duplicate,
                    duplicate=duplicate,
                )
            )
        for table_index in sorted(table_cells):
            rows = table_cells[table_index]
            first = min(rows)
            head_row = header_row(
                {r: {c: t for c, (_, t) in cols.items()} for r, cols in rows.items()}, first
            )
            heads = {c: text for c, (_, text) in rows.get(head_row, {}).items()}
            for row in sorted(rows):
                cells = rows[row]
                parts = []
                for column in sorted(cells):
                    segment, text = cells[column]
                    header = heads.get(column, "")
                    parts.append(f"{header}: {text}" if header else text)
                classification, reasons = classify(
                    "；".join(parts), kind="table_row" if row != head_row else "table_header"
                )
                units.append(
                    Unit(
                        key=f"pdf:t:{document.id}:{page}:{table_index}:{row}",
                        document_id=document.id,
                        filename=document.filename,
                        segment_ids=[cells[c][0].id for c in sorted(cells)],
                        locator={
                            "page": page,
                            "table": table_index,
                            "row": row,
                            "columns": sorted(cells),
                            "headers": {str(c): heads.get(c, "") for c in sorted(heads)},
                        },
                        text="；".join(parts),
                        kind="table_row" if row != head_row else "table_header",
                        table=table_index,
                        row=row,
                        columns=sorted(cells),
                        headers={str(c): heads.get(c, "") for c in sorted(heads)},
                        classification=classification,
                        reasons=reasons,
                        eligible=row != head_row and classification in ALLOWED,
                    )
                )
    return units


def build_text_units(document, segments):
    units = []
    section = ""
    for segment in segments:
        locator = segment.locator or {}
        section = locator.get("section") or section
        stripped = segment.text.strip()
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            units.append(
                Unit(
                    key=f"text:h:{document.id}:{locator.get('line', 0)}",
                    document_id=document.id,
                    filename=document.filename,
                    segment_ids=[segment.id],
                    locator=dict(locator),
                    text=heading_match.group(2),
                    kind="heading",
                    section=section,
                    classification="reference",
                    reasons=["heading_not_capability"],
                    eligible=False,
                )
            )
            continue
        bullet = bool(re.match(r"^(?:[-*•]|\d+[.、)])\s+", stripped))
        kind = (
            "bullet"
            if bullet
            else "parameter_paragraph"
            if ":" in stripped or "：" in stripped
            else "paragraph"
        )
        classification, reasons = classify(stripped, kind=kind, section=section)
        units.append(
            Unit(
                key=f"text:{document.id}:{locator.get('line', 0)}",
                document_id=document.id,
                filename=document.filename,
                segment_ids=[segment.id],
                locator=dict(locator),
                text=segment.text,
                kind=kind,
                section=section,
                classification=classification,
                reasons=reasons,
                eligible=classification in ALLOWED,
            )
        )
    return units


def gap_units(document, segments):
    units = []
    for page in (document.parse_details or {}).get("pages", []):
        if page.get("status") != "parsed":
            units.append(
                Unit(
                    key=f"pdf:gap:{document.id}:{page.get('page')}",
                    document_id=document.id,
                    filename=document.filename,
                    segment_ids=[],
                    locator={"page": page.get("page"), "gap": page.get("status")},
                    text="",
                    kind="ocr_gap",
                    classification="reference",
                    reasons=[f"page_gap:{page.get('status')}"],
                    eligible=False,
                    ocr_required=page.get("status") == "ocr_required",
                )
            )
    if document.parse_status == "ocr_required" and not units:
        units.append(
            Unit(
                key=f"gap:{document.id}",
                document_id=document.id,
                filename=document.filename,
                segment_ids=[],
                locator={"gap": "ocr_required"},
                text="",
                kind="ocr_gap",
                classification="reference",
                reasons=["document_ocr_required"],
                eligible=False,
                ocr_required=True,
            )
        )
    return units


def build_document_units(document, segments):
    suffix = (
        ("." + document.filename.rsplit(".", 1)[-1].lower()) if "." in document.filename else ""
    )
    if suffix == ".docx":
        units = build_docx_units(document, segments)
    elif suffix == ".xlsx":
        units = build_xlsx_units(document, segments)
    elif suffix == ".pdf":
        units = build_pdf_units(document, segments)
    else:
        units = build_text_units(document, segments)
    return units + gap_units(document, segments)


def load_documents(db, context, limit=None):
    query = (
        select(Document)
        .where(
            Document.snapshot_id == context.snapshot_id,
            Document.category_id == context.category_id,
            Document.parse_status.in_(["parsed", "partial", "ocr_required"]),
        )
        .order_by(Document.id)
    )
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


def build_units(db, context, *, max_documents=None, max_units=None):
    documents = load_documents(db, context, max_documents)
    units = []
    truncated = False
    for document in documents:
        segments = list(
            db.scalars(
                select(DocumentSegment)
                .where(DocumentSegment.document_id == document.id)
                .order_by(DocumentSegment.id)
            )
        )
        units.extend(build_document_units(document, segments))
        if max_units and len(units) > max_units:
            truncated = True
            units = units[:max_units]
            break
    total = len(load_documents(db, context))
    return units, {
        "documents_considered": len(documents),
        "documents_total": total,
        "truncated": truncated or total > len(documents),
    }
