"""P2.1 招标文件结构恢复。

只负责把用户输入恢复为带来源定位的区域，不判断产品是否支持，也不写入
Agent A 的产品知识库。DOCX/XLSX 复用现有 Office 安全校验；PDF 逐页保留
解析缺口。表格列角色优先于关键词，证明材料/商务列不进入技术候选正文。
"""

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document as Docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook

from app.core.config import Settings
from app.core.security import ArchiveRejected
from app.ingestion.doc_parsers import DocumentError, validate_office

HEADER_ROLE_RULES = [
    (
        "proof",
        re.compile(r"证明材料|证明文件|检测报告|测试报告|截图|证书|承诺函|授权函|资质", re.I),
    ),
    ("commercial", re.compile(r"商务|报价|价格|付款|售后|质保|培训|交付|合同", re.I)),
    ("seq", re.compile(r"序号|编号|序列|no\.?|item", re.I)),
    ("category", re.compile(r"功能分类|功能类别|类别|分类|category", re.I)),
    (
        "technical",
        re.compile(
            r"技术参数|技术要求|技术指标|技术规格|功能要求|功能描述|参数说明|参数要求"
            r"|招标参数|技术需求|规格|指标|要求|说明|参数",
            re.I,
        ),
    ),
    ("name", re.compile(r"功能项|功能名称|项目名称|项目|功能|模块|名称", re.I)),
    ("remark", re.compile(r"备注|备注说明|说明", re.I)),
]
HEADER_WORDS = re.compile(
    r"序号|编号|功能|名称|类别|技术参数|技术要求|参数说明|功能要求|招标参数|证明材料|截图|检测报告",
    re.I,
)
SECTION_HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节]\s*)?"
    r"(?:编制说明|使用说明|填写指南|填写说明|注意事项|说明|目录|"
    r"技术参数|技术要求|技术规格|功能要求|参数要求|招标参数|"
    r"服务与商务|售后服务|商务条款|投标人须知|评标办法)"
    r"(?:要求|表|说明|条款|一览表|如下)?$",
    re.I,
)


@dataclass
class Region:
    kind: str
    text: str
    locator: dict
    section: str = ""
    headers: list[str] | None = None
    cells: list[str] | None = None
    roles: list[str] | None = None
    table_id: str | None = None
    row: int | None = None
    flags: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    source_type: str
    title: str
    regions: list[Region]
    details: dict = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)


class TenderParseError(Exception):
    def __init__(self, code: str):
        self.code = code


UNSAFE_OFFICE_PARTS = (
    "vbaproject.bin",
    "/activex/",
    "/embeddings/",
    "xl/externallinks/",
    "word/externallinks/",
)


def _validate_tender_office(path: Path):
    """拒绝宏、ActiveX、嵌入对象和外链；公式在读取时不做计算。"""
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                lowered = name.lower()
                if any(part in lowered for part in UNSAFE_OFFICE_PARTS):
                    raise TenderParseError("UNSAFE_OFFICE_CONTENT")
                if not lowered.endswith(".rels"):
                    continue
                try:
                    relationships = archive.read(name).decode("utf-8", "ignore")
                except KeyError:
                    continue
                if re.search(r'TargetMode\s*=\s*["\']External', relationships, re.I):
                    raise TenderParseError("UNSAFE_OFFICE_CONTENT")
    except zipfile.BadZipFile as exc:
        raise TenderParseError("INVALID_OFFICE_CONTAINER") from exc


def role_for(header: str) -> str:
    text = (header or "").strip()
    if re.fullmatch(
        r"(?:功能|项目|模块|名称|功能项|指标项|参数项|项目名称|功能名称|技术参数项|"
        r"技术参数名称|技术指标名称|功能名称|指标名称|参数名称)",
        text,
        re.I,
    ):
        return "name"
    for role, pattern in HEADER_ROLE_RULES:
        if pattern.search(text):
            return role
    return "unknown"


def _nonempty(values):
    return [str(value).strip() for value in values if value is not None and str(value).strip()]


def _header_score(values):
    texts = {str(value).strip() for value in values if value and str(value).strip()}
    if len(texts) < 2:
        return 0
    return sum(1 for value in texts if HEADER_WORDS.search(value))


def _find_header(rows: dict[int, list[str]], first: int, limit: int = 8) -> int:
    best_row, best_score = first, 0
    for row in sorted(rows):
        if row < first:
            continue
        if row > first + limit:
            break
        score = _header_score(rows[row])
        if score > best_score:
            best_row, best_score = row, score
    return best_row


def _row_values(cells) -> list[str]:
    seen = set()
    values = []
    for cell in cells:
        key = id(cell._tc)
        if key in seen:
            values.append("")
            continue
        seen.add(key)
        values.append(cell.text or "")
    return values


def _is_repeated_header(cells: list[str], headers: list[str]) -> bool:
    nonempty = _nonempty(cells)
    if len(nonempty) < 2:
        return False
    named = sum(1 for value in nonempty if role_for(value) != "unknown")
    return named >= 2 and any(value in headers for value in nonempty)


def _table_rows(
    rows: dict[int, list[str]],
    *,
    table_id: str,
    section: str,
    locator_base: dict,
    title_row: int | None = None,
) -> tuple[list[Region], dict]:
    if not rows:
        return [], {}
    first = min(rows)
    header_row = _find_header(rows, first)
    header_values = rows.get(header_row, [])
    width = max(len(values) for values in rows.values())
    headers = [header_values[i] if i < len(header_values) else "" for i in range(width)]
    roles = [role_for(value) for value in headers]
    header_text = " | ".join(value for value in headers if value.strip())
    regions = []
    for row in sorted(rows):
        if row >= header_row:
            break
        values = rows[row]
        text = " | ".join(value for value in values if value.strip())
        if not text:
            continue
        regions.append(
            Region(
                kind="table_title",
                text=text,
                locator={**locator_base, "table": table_id, "row": row, "table_title": True},
                section=section,
                headers=headers,
                cells=values,
                roles=roles,
                table_id=table_id,
                row=row,
                flags={"table_title": True},
            )
        )
    if header_text:
        regions.append(
            Region(
                kind="table_header",
                text=header_text,
                locator={
                    **locator_base,
                    "table": table_id,
                    "row": header_row,
                    "headers": {str(i): value for i, value in enumerate(headers) if value},
                },
                section=section,
                headers=headers,
                cells=headers,
                roles=roles,
                table_id=table_id,
                row=header_row,
                flags={"table_header": True},
            )
        )
    for row in sorted(rows):
        if row <= header_row:
            continue
        cells = rows[row]
        values = [cells[i] if i < len(cells) else "" for i in range(width)]
        if not any(value.strip() for value in values):
            continue
        if row == title_row:
            continue
        repeated = _is_repeated_header(values, headers)
        locator = {
            **locator_base,
            "table": table_id,
            "row": row,
            "headers": {str(i): value for i, value in enumerate(headers) if value},
            "columns": [i for i, value in enumerate(values) if value.strip()],
        }
        regions.append(
            Region(
                kind="table_row",
                text=" | ".join(value for value in values if value.strip()),
                locator=locator,
                section=section,
                headers=headers,
                cells=values,
                roles=roles,
                table_id=table_id,
                row=row,
                flags={"repeated_header": repeated},
            )
        )
    return regions, {"header_row": header_row, "headers": headers, "roles": roles}


def parse_docx(path: Path, settings: Settings) -> ParsedDocument:
    validate_office(path, settings, "word/document.xml")
    _validate_tender_office(path)
    doc = Docx(path)
    regions = []
    section = ""
    paragraph_index = 0
    table_index = 0
    table_details = []
    for block in doc.element.body.iterchildren():
        if block.tag == qn("w:p"):
            paragraph_index += 1
            paragraph = Paragraph(block, doc)
            text = (paragraph.text or "").strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style else ""
            is_heading = (
                style.startswith("Heading")
                or bool(
                    re.match(
                        r"^第[一二三四五六七八九十百]+[章节]|"
                        r"^[一二三四五六七八九十]+、",
                        text,
                    )
                )
                or bool(SECTION_HEADING.match(text.strip("：: ")))
            )
            if is_heading:
                section = text[:240]
            regions.append(
                Region(
                    kind="heading" if is_heading else "paragraph",
                    text=text,
                    locator={
                        "paragraph": paragraph_index,
                        "section": section,
                        "style": style,
                    },
                    section=section,
                )
            )
        elif block.tag == qn("w:tbl"):
            table_index += 1
            table = Table(block, doc)
            rows = {index: _row_values(row.cells) for index, row in enumerate(table.rows, 1)}
            peculiar, detail = _table_rows(
                rows,
                table_id=f"docx-table-{table_index}",
                section=section,
                locator_base={"kind": "docx_table", "section": section},
            )
            regions.extend(peculiar)
            table_details.append({"table": table_index, **detail})
    return ParsedDocument(
        source_type="docx",
        title=_title(regions),
        regions=regions,
        details={"paragraphs": paragraph_index, "tables": table_details},
    )


def _title(regions: list[Region]) -> str:
    for region in regions:
        if region.kind == "heading":
            return region.text[:200]
    return ""


def _sheet_rows(sheet, settings: Settings):
    if sheet.max_row and sheet.max_row > settings.tender_max_rows:
        raise TenderParseError("SHEET_ROW_LIMIT")
    if sheet.max_column and sheet.max_column > 200:
        raise TenderParseError("SHEET_COLUMN_LIMIT")
    rows = {}
    for row in sheet.iter_rows():
        values = ["" if cell.value is None else str(cell.value) for cell in row]
        if any(value.strip() for value in values):
            rows[row[0].row] = values
    return rows


def _fill_xlsx_merges(sheet, rows: dict[int, list[str]], header_row: int, roles: list[str]):
    """只继承表头与分类/范围类合并单元格；技术正文保持原位置。"""
    for merged in sheet.merged_cells.ranges:
        anchor = rows.get(merged.min_row, [])
        if merged.min_col - 1 >= len(anchor):
            continue
        value = anchor[merged.min_col - 1]
        if not value:
            continue
        for row in range(merged.min_row, merged.max_row + 1):
            target = rows.setdefault(row, [])
            for col in range(merged.min_col, merged.max_col + 1):
                role = roles[col - 1] if col - 1 < len(roles) else "unknown"
                if row <= header_row or role in {"name", "seq", "technical", "category"}:
                    if col - 1 >= len(target):
                        target.extend([""] * (col - len(target)))
                    if not target[col - 1]:
                        target[col - 1] = value


def parse_xlsx(path: Path, settings: Settings) -> ParsedDocument:
    validate_office(path, settings, "xl/workbook.xml")
    _validate_tender_office(path)
    # data_only=True 只读取缓存值，绝不计算公式；keep_links=False 不加载外部工作簿。
    book = load_workbook(path, read_only=False, data_only=True, keep_links=False)
    try:
        if len(book.worksheets) > settings.tender_max_sheets:
            raise TenderParseError("SHEET_COUNT_LIMIT")
        regions = []
        sheets = []
        for sheet in book.worksheets:
            rows = _sheet_rows(sheet, settings)
            if not rows:
                sheets.append({"sheet": sheet.title, "rows": 0, "status": "empty"})
                continue
            first = min(rows)
            header_row = _find_header(rows, first)
            width = max(len(values) for values in rows.values())
            header_values = rows.get(header_row, [])
            headers = [header_values[i] if i < len(header_values) else "" for i in range(width)]
            roles = [role_for(value) for value in headers]
            _fill_xlsx_merges(sheet, rows, header_row, roles)
            table_regions, detail = _table_rows(
                rows,
                table_id=f"xlsx-sheet-{sheet.title}",
                section=sheet.title,
                locator_base={"kind": "xlsx", "sheet": sheet.title},
                title_row=None,
            )
            regions.extend(table_regions)
            sheets.append(
                {
                    "sheet": sheet.title,
                    "rows": len(rows),
                    "header_row": header_row,
                    "headers": headers,
                    "formulas_preserved": sum(
                        1
                        for values in rows.values()
                        for value in values
                        if str(value).startswith("=")
                    ),
                }
            )
        return ParsedDocument(
            source_type="xlsx",
            title=book.sheetnames[0] if book.sheetnames else "",
            regions=regions,
            details={"sheets": sheets, "tables": len(sheets)},
        )
    finally:
        book.close()


def _pdf_page_text(page) -> str:
    return page.extract_text() or ""


def _remove_repeated_edges(lines: list[str], repeated: set[str]) -> list[str]:
    kept = []
    for index, line in enumerate(lines):
        if index < 2 or index >= len(lines) - 2:
            if line.strip() in repeated:
                continue
        kept.append(line)
    return kept


def parse_pdf(path: Path, settings: Settings) -> ParsedDocument:
    raw = path.read_bytes()
    if raw[:5] != b"%PDF-":
        raise TenderParseError("INVALID_PDF")
    if b"/JavaScript" in raw or b"/EmbeddedFile" in raw:
        raise TenderParseError("UNSAFE_PDF_CONTENT")
    regions = []
    gaps = []
    page_details = []
    table_index = 0
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > settings.tender_max_pages:
            raise TenderParseError("PAGE_LIMIT")
        page_texts = [_pdf_page_text(page) for page in pdf.pages]
        repeated = set()
        edge_lines = []
        for text in page_texts:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            edge_lines.extend(lines[:2] + lines[-2:])
        for line in set(edge_lines):
            if line and sum(line == candidate for candidate in edge_lines) >= 2:
                repeated.add(line)
        for page_number, page in enumerate(pdf.pages, 1):
            text = page_texts[page_number - 1]
            if not text.strip():
                status = "ocr_required" if page.images else "empty_page"
                gaps.append({"page": page_number, "status": status})
                page_details.append({"page": page_number, "status": status})
                page.close()
                continue
            lines = _remove_repeated_edges(text.splitlines(), repeated)
            cell_texts = set()
            found_tables = page.find_tables()
            for local_index, table in enumerate(found_tables, 1):
                table_index += 1
                extracted = table.extract() or []
                rows = {
                    row_index: [str(value or "") for value in values]
                    for row_index, values in enumerate(extracted, 1)
                }
                for values in rows.values():
                    cell_texts.update(value.strip() for value in values if value.strip())
                page_regions, _ = _table_rows(
                    rows,
                    table_id=f"pdf-table-{table_index}",
                    section="",
                    locator_base={
                        "kind": "pdf",
                        "page": page_number,
                        "page_table": local_index,
                    },
                )
                regions.extend(page_regions)
            remainder = text
            for cell in sorted(cell_texts, key=len, reverse=True):
                remainder = remainder.replace(cell, " ")
            for line_index, line in enumerate(remainder.splitlines(), 1):
                if line.strip():
                    regions.append(
                        Region(
                            kind="paragraph",
                            text=line.strip(),
                            locator={"kind": "pdf", "page": page_number, "line": line_index},
                        )
                    )
            page_details.append(
                {
                    "page": page_number,
                    "status": "parsed",
                    "tables": len(found_tables),
                    "duplicate_cell_text_removed": bool(cell_texts),
                }
            )
            page.close()
    for gap in gaps:
        regions.append(
            Region(
                kind="gap",
                text="",
                locator={"kind": "pdf", "page": gap["page"], "gap": gap["status"]},
                flags={"parse_gap": gap["status"]},
            )
        )
    return ParsedDocument(
        source_type="pdf",
        title="",
        regions=regions,
        details={"pages": page_details},
        gaps=gaps,
    )


def parse_text(text: str) -> ParsedDocument:
    regions = []
    section = ""
    for line_number, raw in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1
    ):
        line = raw.strip()
        if not line:
            continue
        if re.match(
            r"^(?:#{1,6}\s+|第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、)",
            line,
        ) or SECTION_HEADING.match(line.strip("：: ")):
            section = line[:240]
            regions.append(
                Region(
                    kind="heading",
                    text=line.lstrip("# ").strip(),
                    locator={"kind": "text", "line": line_number, "section": section},
                    section=section,
                )
            )
            continue
        regions.append(
            Region(
                kind="paragraph",
                text=line,
                locator={"kind": "text", "line": line_number, "section": section},
                section=section,
            )
        )
    return ParsedDocument(
        source_type="text", title="", regions=regions, details={"lines": len(regions)}
    )


def parse_file(path: Path, suffix: str, settings: Settings) -> ParsedDocument:
    try:
        if suffix == ".docx":
            return parse_docx(path, settings)
        if suffix == ".xlsx":
            return parse_xlsx(path, settings)
        if suffix == ".pdf":
            return parse_pdf(path, settings)
    except DocumentError as exc:
        raise TenderParseError(exc.code) from exc
    except ArchiveRejected as exc:
        raise TenderParseError(exc.code) from exc
    raise TenderParseError("UNSUPPORTED_DOCUMENT")


def parse_upload(path: Path, settings: Settings) -> ParsedDocument:
    suffix = path.suffix.lower()
    return parse_file(path, suffix, settings)
