import zipfile
from pathlib import Path

import openpyxl
import pdfplumber
from docx import Document as Docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.security import ArchiveRejected
from app.ingestion.archive_guard import inspect_entries, preflight_directory
from app.ingestion.text import decode_source


class DocumentError(Exception):
    def __init__(self, code):
        self.code = code


def validate_office(path, settings, required):
    preflight_directory(path, settings)
    with zipfile.ZipFile(path) as archive:
        inspect_entries(archive, settings)
        if required not in archive.namelist():
            raise DocumentError("INVALID_OFFICE_CONTAINER")
        # 提前流式校验 CRC/真实大小；拒绝 DTD，禁用外部实体入口。
        for info in archive.infolist():
            if info.is_dir():
                continue
            count, tail = 0, b""
            with archive.open(info) as member:
                while block := member.read(65536):
                    count += len(block)
                    if count > settings.max_file_bytes:
                        raise DocumentError("DOCUMENT_EXPANSION_LIMIT")
                    upper = (tail + block).upper()
                    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                        raise DocumentError("XML_ENTITY_FORBIDDEN")
                    tail = block[-32:]


def parse_document(path: Path, suffix: str, settings, details=None):
    if path.stat().st_size > settings.document_max_bytes:
        raise DocumentError("DOCUMENT_TOO_LARGE")
    count = chars = 0
    details = details if details is not None else {}

    def checked(locator, text):
        nonlocal count, chars
        count += 1
        chars += len(text)
        if count > settings.document_max_segments or chars > settings.document_max_text_chars:
            raise DocumentError("DOCUMENT_OUTPUT_LIMIT")
        return locator, text

    try:
        if suffix == ".docx":
            validate_office(path, settings, "word/document.xml")
            doc = Docx(path)
            paragraph_index = table_index = 0
            section = ""
            for block in doc.element.body.iterchildren():
                if block.tag == qn("w:p"):
                    paragraph_index += 1
                    p = Paragraph(block, doc)
                    if p.style and p.style.name.startswith("Heading"):
                        section = p.text
                    if p.text.strip():
                        yield checked({"paragraph": paragraph_index, "section": section}, p.text)
                elif block.tag == qn("w:tbl"):
                    table_index += 1
                    for row_index, row in enumerate(Table(block, doc).rows, 1):
                        for column, cell in enumerate(row.cells, 1):
                            if cell.text.strip():
                                yield checked(
                                    {
                                        "table": table_index,
                                        "row": row_index,
                                        "column": column,
                                        "section": section,
                                    },
                                    cell.text,
                                )
        elif suffix == ".pdf":
            if not path.read_bytes()[:5] == b"%PDF-":
                raise DocumentError("INVALID_PDF")
            with pdfplumber.open(path) as doc:
                if len(doc.pages) > 500:
                    raise DocumentError("DOCUMENT_PAGE_LIMIT")
                details["pages"] = []
                for page_index, page in enumerate(doc.pages, 1):
                    text = page.extract_text() or ""
                    if not text.strip():
                        page_status = "ocr_required" if page.images else "empty_page"
                        details["pages"].append({"page": page_index, "status": page_status})
                        page.close()
                        continue
                    details["pages"].append({"page": page_index, "status": "parsed"})
                    yield checked({"page": page_index, "kind": "page_text"}, text)
                    for table_index, table in enumerate(page.find_tables(), 1):
                        for row_index, row in enumerate(table.extract(), 1):
                            for column, value in enumerate(row, 1):
                                if value:
                                    yield checked(
                                        {
                                            "page": page_index,
                                            "table": table_index,
                                            "bbox": list(table.bbox),
                                            "row": row_index,
                                            "column": column,
                                        },
                                        value,
                                    )
                    page.close()
        elif suffix == ".xlsx":
            validate_office(path, settings, "xl/workbook.xml")
            book = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
            # read_only 工作表不暴露 merged_cells；从已限额 XML 读取合并定位。
            from defusedxml.ElementTree import fromstring

            merge_ranges = {}
            with zipfile.ZipFile(path) as archive:
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                relns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
                rels = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                targets = {
                    r.attrib["Id"]: r.attrib["Target"] for r in rels.findall(relns + "Relationship")
                }
                workbook = fromstring(archive.read("xl/workbook.xml"))
                for sheet in workbook.findall("s:sheets/s:sheet", ns):
                    rid = sheet.attrib[
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    ]
                    target = targets.get(rid, "")
                    member = target.lstrip("/") if target.startswith("/") else "xl/" + target
                    if member in archive.namelist():
                        xml = fromstring(archive.read(member))
                        merge_ranges[sheet.attrib["name"]] = [
                            c.attrib["ref"] for c in xml.findall("s:mergeCells/s:mergeCell", ns)
                        ]
            details["sheet_merged_ranges"] = merge_ranges
            merge_anchors = {
                name: {ref.split(":")[0]: ref for ref in refs}
                for name, refs in merge_ranges.items()
            }
            try:
                for sheet in book:
                    if (
                        sheet.max_row
                        and sheet.max_row * (sheet.max_column or 1)
                        > settings.document_max_segments * 10
                    ):
                        raise DocumentError("SHEET_DIMENSION_LIMIT")
                    for row in sheet.iter_rows():
                        for cell in row:
                            if cell.value is not None:
                                # 公式仅作为原文保存，不计算，也不跟随外部链接。
                                yield checked(
                                    {
                                        "sheet": sheet.title,
                                        "cell": cell.coordinate,
                                        "merged_ranges": (
                                            [merge_anchors[sheet.title][cell.coordinate]]
                                            if cell.coordinate in merge_anchors.get(sheet.title, {})
                                            else []
                                        ),
                                        "formula_not_evaluated": cell.data_type == "f",
                                    },
                                    str(cell.value),
                                )
            finally:
                book.close()
        elif suffix in {".md", ".txt"}:
            text, encoding = decode_source(path.read_bytes())
            section = ""
            for line_index, line in enumerate(text.splitlines(), 1):
                if line.startswith("#"):
                    section = line.lstrip("# ")
                if line.strip():
                    yield checked(
                        {"line": line_index, "section": section, "encoding": encoding}, line
                    )
        else:
            raise DocumentError("UNSUPPORTED_DOCUMENT")
        if count == 0:
            raise DocumentError("OCR_REQUIRED" if suffix == ".pdf" else "EMPTY_DOCUMENT")
    except DocumentError:
        raise
    except ArchiveRejected as exc:
        raise DocumentError(exc.code) from exc
    except UnicodeError as exc:
        raise DocumentError("INVALID_ENCODING") from exc
    except Exception as exc:
        # 不把解析器异常中的敏感内容写到 API。
        if "password" in type(exc).__name__.lower():
            raise DocumentError("ENCRYPTED_DOCUMENT") from exc
        raise DocumentError("INVALID_DOCUMENT") from exc
