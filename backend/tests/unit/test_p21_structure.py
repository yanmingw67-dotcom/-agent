"""P2.1 结构化文件测试：Word 表格、Excel 多 Sheet、PDF 跨页与解析缺口。"""

import io
import zipfile

import pytest
from docx import Document

from app.core.config import get_settings
from app.tender.rules import extract
from app.tender.structure import TenderParseError, parse_docx, parse_pdf, parse_xlsx
from tests.p21_assets import (
    pdf_lines_document,
    pdf_pages,
    pdf_with_empty_page,
    waf_docx,
    waf_xlsx,
)


def test_word_table_one_row_per_parameter_and_repeated_header(tmp_path):
    path = tmp_path / "waf.docx"
    path.write_bytes(waf_docx())
    parsed = parse_docx(path, get_settings())
    result = extract(parsed)
    assert len(result.candidates) == 15
    assert sum(len(item.atomic_requirements) for item in result.candidates) == 45
    assert result.coverage_status == "complete"
    assert any(
        region.kind == "table_row" and region.flags.get("repeated_header")
        for region in parsed.regions
    )
    assert any(record.reason_code == "proof_or_commercial_column" for record in result.coverage)


def test_word_merged_cells_and_continuation(tmp_path):
    path = tmp_path / "waf-merged.docx"
    path.write_bytes(waf_docx(merged=True))
    result = extract(parse_docx(path, get_settings()))
    assert len(result.candidates) == 15
    assert any("（4）支持Web攻击防护能力1D" in item.original_text for item in result.candidates)


def test_plain_section_heading_and_function_requirement_column(tmp_path):
    path = tmp_path / "plain.docx"
    doc = Document()
    doc.add_paragraph("技术参数要求")
    table = doc.add_table(rows=1, cols=3)
    for cell, value in zip(table.rows[0].cells, ["序号", "功能要求", "证明材料"], strict=True):
        cell.text = value
    for index, value in enumerate(["支持 HTTP 解析。", "支持 SQL 注入检测。"], 1):
        cells = table.add_row().cells
        cells[0].text = str(index)
        cells[1].text = value
        cells[2].text = "提供截图"
    doc.save(path)
    result = extract(parse_docx(path, get_settings()))
    assert len(result.candidates) == 2
    assert result.candidates[0].original_number == "1"
    assert result.candidates[0].parameter_name == "HTTP 解析"


def test_macro_is_rejected_without_parsing(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(waf_docx())
    target = tmp_path / "macro.docx"
    with (
        zipfile.ZipFile(source) as original,
        zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output,
    ):
        for info in original.infolist():
            output.writestr(info, original.read(info.filename))
        output.writestr("word/vbaProject.bin", io.BytesIO(b"macro").getvalue())
    with pytest.raises(TenderParseError) as error:
        parse_docx(target, get_settings())
    assert error.value.code == "UNSAFE_OFFICE_CONTENT"


def test_xlsx_multiple_sheets_and_merged_category(tmp_path):
    path = tmp_path / "waf.xlsx"
    path.write_bytes(waf_xlsx())
    result = extract(parse_xlsx(path, get_settings()))
    assert len(result.candidates) == 5
    assert [item.parameter_name for item in result.candidates] == [
        "Web攻击防护",
        "命令注入防护",
        "漏洞防护",
        "身份认证",
        "日志审计",
    ]
    assert not any("售后服务" in item.parameter_name for item in result.candidates)
    assert any(record.reason_code == "non_technical_section" for record in result.coverage)


def test_pdf_cross_page_and_empty_page_gap(tmp_path):
    path = tmp_path / "waf.pdf"
    path.write_bytes(pdf_lines_document(pdf_pages()))
    parsed = parse_pdf(path, get_settings())
    result = extract(parsed)
    assert len(result.candidates) == 5
    assert result.coverage_status == "complete"

    gap_path = tmp_path / "scan.pdf"
    gap_path.write_bytes(pdf_with_empty_page())
    gap_result = extract(parse_pdf(gap_path, get_settings()))
    assert gap_result.coverage_status == "incomplete"
    assert gap_result.gaps
    assert gap_result.candidates
