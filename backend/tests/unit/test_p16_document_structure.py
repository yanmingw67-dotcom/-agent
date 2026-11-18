"""P1.6 文档结构恢复：列角色、合并单元格、章节排除与解析缺口。"""

from types import SimpleNamespace

from app.core.config import Settings
from app.ingestion.doc_parsers import parse_document
from app.knowledge.document_structure import build_document_units
from tests import p16_assets

SETTINGS = Settings(admin_token="a" * 40, read_token="r" * 40)


def parse_units(data, suffix, filename, parse_details=None, status="parsed"):
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        segments = [
            SimpleNamespace(id=f"s{index}", locator=locator, text=text)
            for index, (locator, text) in enumerate(parse_document(path, suffix, SETTINGS), start=1)
        ]
    finally:
        path.unlink(missing_ok=True)
    document = SimpleNamespace(
        id="doc-1",
        filename=filename,
        parse_status=status,
        parse_details=parse_details or {},
    )
    return build_document_units(document, segments)


def test_docx_table_rows_use_capability_columns_only():
    units = parse_units(p16_assets.docx_manual(), ".docx", "manual.docx")
    rows = {unit.text.split("；")[0]: unit for unit in units if unit.kind == "table_row"}
    assert rows["功能名称: 多模式部署"].classification == "deployment"
    assert rows["功能名称: 多租户隔离"].classification == "current_capability"
    for unit in rows.values():
        assert "需提供截图" not in unit.text
        assert "检测报告" not in unit.text
        assert "部分支持" not in unit.text
        assert "context_column:proof_material" in unit.reasons
        assert "context_column:competitor" in unit.reasons


def test_docx_future_and_legal_sections_are_not_capabilities():
    units = parse_units(p16_assets.docx_manual(), ".docx", "manual.docx")
    future = [unit for unit in units if unit.section == "后续版本规划"]
    legal = [unit for unit in units if unit.section == "商标与版权声明"]
    assert future and all(
        unit.classification == "future_plan" for unit in future if unit.kind != "heading"
    )
    assert legal and all(not unit.eligible for unit in legal)


def test_xlsx_column_roles_and_future_sheet():
    units = parse_units(p16_assets.xlsx_capabilities(), ".xlsx", "matrix.xlsx")
    main = [unit for unit in units if unit.sheet == "功能参数"]
    by_name = {unit.text.split("；")[0]: unit for unit in main}
    tenant = by_name["功能名称: 多租户策略隔离"]
    assert tenant.classification == "current_capability"
    assert tenant.eligible is True
    assert "需提供承诺函" not in tenant.text
    assert by_name["功能名称: 智能编排"].classification == "future_plan"
    assert by_name["功能名称: 智能编排"].eligible is False
    assert by_name["功能名称: 售后服务"].classification == "commercial"
    plan = [unit for unit in units if unit.sheet == "版本规划"]
    assert plan and all(not unit.eligible for unit in plan)


def test_docx_table_rows_are_bound_to_their_own_header_and_row():
    units = parse_units(p16_assets.docx_manual(), ".docx", "manual.docx")
    rows = [unit for unit in units if unit.kind == "table_row"]
    assert rows
    for unit in rows:
        assert unit.table == 1
        assert unit.locator["headers"]["2"] == "功能名称"
        assert set(unit.locator["columns"]) <= {1, 2, 3, 4, 5}
    headers = [unit for unit in units if unit.kind == "table_header"]
    assert len(headers) == 1
    assert headers[0].eligible is False


def test_xlsx_merged_category_is_inherited_only_as_context():
    units = parse_units(p16_assets.xlsx_capabilities(), ".xlsx", "matrix.xlsx")
    merged = [unit for unit in units if unit.sheet == "功能参数" and unit.merged_context]
    assert merged
    assert all("访问控制" in unit.merged_context.values() for unit in merged)
    assert all("访问控制" not in unit.text for unit in merged)


def test_pdf_page_text_and_dual_extraction():
    units = parse_units(p16_assets.pdf_document(), ".pdf", "manual.pdf")
    pages = [unit for unit in units if unit.kind == "page_text"]
    assert [unit.classification for unit in pages] == ["deployment", "current_capability"]
    assert [unit.locator["page"] for unit in pages] == [1, 2]
    assert all(unit.eligible for unit in pages)


def test_parsed_gap_is_reported_not_guessed():
    units = parse_units(
        p16_assets.pdf_document(),
        ".pdf",
        "scan.pdf",
        parse_details={
            "pages": [{"page": 1, "status": "ocr_required"}, {"page": 2, "status": "parsed"}]
        },
    )
    gaps = [unit for unit in units if unit.kind == "ocr_gap"]
    assert gaps and gaps[0].ocr_required is True
    assert gaps[0].eligible is False


def test_markdown_bullets_and_future_section():
    units = parse_units(p16_assets.markdown_document(), ".md", "readme.md")
    bullets = [unit for unit in units if unit.kind == "bullet"]
    assert {unit.classification for unit in bullets} == {"deployment", "current_capability"}
    future = [unit for unit in units if unit.section == "后续规划"]
    assert future and all(
        unit.classification == "future_plan" for unit in future if unit.kind != "heading"
    )


def test_repeated_table_headers_are_independent_and_not_capabilities():
    import io

    from docx import Document

    document = Document()
    for caption in ["第一页功能表", "第二页续接表"]:
        document.add_paragraph(caption)
        table = document.add_table(rows=1, cols=3)
        for cell, value in zip(
            table.rows[0].cells, ["功能名称", "技术参数", "证明材料"], strict=True
        ):
            cell.text = value
        row = table.add_row().cells
        for cell, value in zip(row, [caption + "能力", "支持当前能力", "需提供截图"], strict=True):
            cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    units = parse_units(buffer.getvalue(), ".docx", "repeat.docx")
    headers = [unit for unit in units if unit.kind == "table_header"]
    rows = [unit for unit in units if unit.kind == "table_row"]
    assert len(headers) == 2
    assert len(rows) == 2
    assert {unit.table for unit in headers} == {1, 2}
    assert {unit.table for unit in rows} == {1, 2}
    assert all(not unit.eligible for unit in headers)
    assert all("需提供截图" not in unit.text for unit in rows)
    assert all(unit.locator["headers"]["1"] == "功能名称" for unit in rows)
