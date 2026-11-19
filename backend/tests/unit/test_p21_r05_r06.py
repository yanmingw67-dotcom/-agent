"""P2.1 第二轮补充整改：R05 锚点、R06 混合条款与开放枚举语义。"""

import io
import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Pt

import app.tender.structure as structure
from app.core.config import get_settings
from app.tender.rules import extract
from app.tender.structure import parse_docx, parse_pdf, parse_text, parse_xlsx
from tests.p21_assets import waf_docx, waf_xlsx


def run_text(text):
    return extract(parse_text(text))


def assert_anchors(candidate):
    for requirement in candidate.atomic_requirements:
        start = requirement["source_span"]["start"]
        end = requirement["source_span"]["end"]
        assert candidate.original_text[start:end] == requirement["original_text"]


def mixed_docx() -> bytes:
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading("技术参数表", level=1)
    table = doc.add_table(rows=1, cols=4)
    for cell, value in zip(
        table.rows[0].cells, ["序号", "功能名称", "技术参数", "证明材料"], strict=True
    ):
        cell.text = value
    cells = table.add_row().cells
    cells[0].text = "1"
    cells[1].text = "HTTP 防护"
    cells[2].text = (
        "（1）支持 HTTP 解析，并提供功能截图；\n（2）支持 SQL 注入检测，并提供检测报告。"
    )
    cells[3].text = "功能截图"
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def pdf_table_document() -> bytes:
    """生成带表格线的 ASCII PDF；仅用于单元格结构和锚点单元测试。"""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    ]
    cells = [
        ("No", 55, 700),
        ("Name", 120, 700),
        ("Technical", 250, 700),
        ("Proof", 520, 700),
        ("1", 55, 670),
        ("HTTP", 120, 670),
        ("(1) Support HTTP parsing.", 250, 670),
        ("Screenshot", 520, 670),
    ]
    stream = b"BT /F1 9 Tf "
    for text, x, y in cells:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream += f"1 0 0 1 {x} {y} Tm ({escaped}) Tj ".encode()
    stream += b"ET "
    for y in (710, 685, 655):
        stream += f"q 0.5 w 50 {y} m 580 {y} l S Q ".encode()
    for x in (50, 100, 200, 400, 580):
        stream += f"q 0.5 w {x} 655 m {x} 710 l S Q ".encode()
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    data += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    ).encode()
    return data


def test_r05_word_table_anchors_include_sequence_and_name_prefix():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "waf.docx"
        path.write_bytes(waf_docx())
        result = extract(parse_docx(path, get_settings()))
    assert len(result.candidates) == 15
    assert sum(len(candidate.atomic_requirements) for candidate in result.candidates) == 45
    for candidate in result.candidates:
        assert candidate.original_text.startswith(candidate.original_number + " ")
        assert_anchors(candidate)


def test_r05_word_merged_continuation_keeps_all_anchors():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "waf-merged.docx"
        path.write_bytes(waf_docx(merged=True))
        result = extract(parse_docx(path, get_settings()))
    assert len(result.candidates) == 15
    merged = [item for item in result.candidates if "能力1D" in item.original_text]
    assert len(merged) == 1
    for candidate in result.candidates:
        assert_anchors(candidate)


def test_r05_excel_table_anchors_use_technical_body_coordinates():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "waf.xlsx"
        path.write_bytes(waf_xlsx())
        result = extract(parse_xlsx(path, get_settings()))
    assert len(result.candidates) == 5
    for candidate in result.candidates:
        assert_anchors(candidate)
        assert candidate.parameter_name in candidate.original_text


def test_r05_pdf_table_anchors_use_technical_body_coordinates(monkeypatch):
    monkeypatch.setattr(
        structure,
        "role_for",
        lambda header: {
            "No": "seq",
            "Name": "name",
            "Technical": "technical",
            "Proof": "proof",
        }.get(header, "unknown"),
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "table.pdf"
        path.write_bytes(pdf_table_document())
        result = extract(parse_pdf(path, get_settings()))
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.parameter_name == "HTTP"
    assert candidate.original_text.startswith("1 HTTP")
    assert_anchors(candidate)


def test_r06_technical_plus_screenshot_only_removes_proof_clause():
    result = run_text("技术参数要求\n1、支持 HTTP 解析，并提供功能截图。")
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert "HTTP 解析" in candidate.original_text
    assert "截图" not in candidate.original_text
    assert [item["original_text"] for item in candidate.atomic_requirements] == ["支持 HTTP 解析"]
    assert_anchors(candidate)
    proof = [item for item in result.coverage if item.reason_code == "proof_material"]
    assert proof and proof[0].source_locator["kind"] == "text"


def test_r06_technical_plus_test_report_only_removes_proof_clause():
    result = run_text("技术参数要求\n1、支持 SQL 注入检测，并提供检测报告。")
    candidate = result.candidates[0]
    assert "SQL 注入检测" in candidate.original_text
    assert "检测报告" not in candidate.original_text
    assert_anchors(candidate)
    assert any(
        item.disposition == "filtered" and item.reason_code == "proof_material"
        for item in result.coverage
    )


def test_r06_standalone_test_report_is_filtered_not_turned_into_parameter():
    result = run_text("技术参数要求\n1、提供检测报告。")
    assert result.candidates == []
    assert any(
        item.disposition == "filtered" and item.reason_code == "proof_material"
        for item in result.coverage
    )


def test_r06_product_log_report_export_is_not_proof_material():
    result = run_text("技术参数要求\n1、支持日志导出 PDF 日志/报告。")
    assert len(result.candidates) == 1
    assert "日志导出 PDF 日志/报告" in result.candidates[0].original_text
    assert not any(
        item.disposition == "filtered" and item.reason_code == "proof_material"
        for item in result.coverage
    )


def test_r06_compound_parameter_proof_clauses_are_filtered_per_subclause():
    result = run_text(
        "1、支持 Web 防护：（1）SQL 注入检测，并提供功能截图；"
        "（2）XSS 检测，并提供检测报告；（3）命令注入检测。"
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert [item["original_text"] for item in candidate.atomic_requirements] == [
        "SQL 注入检测",
        "XSS 检测",
        "命令注入检测",
    ]
    assert_anchors(candidate)
    assert (
        sum(
            1
            for item in result.coverage
            if item.disposition == "filtered" and item.reason_code == "proof_material"
        )
        >= 2
    )


def test_r06_word_table_mixed_technical_and_proof_keeps_technical_text():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "mixed.docx"
        path.write_bytes(mixed_docx())
        result = extract(parse_docx(path, get_settings()))
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert "HTTP 解析" in candidate.original_text
    assert "SQL 注入检测" in candidate.original_text
    assert "截图" not in candidate.original_text
    assert "检测报告" not in candidate.original_text
    assert_anchors(candidate)
    assert any(
        item.disposition == "filtered" and item.reason_code == "proof_material"
        for item in result.coverage
    )


def test_open_enumeration_uncertain_remains_manual_but_not_required():
    candidate = run_text("1、包括但不限于A、B、C。").candidates[0]
    assert candidate.review_required is True
    assert all(item["is_mandatory"] is False for item in candidate.atomic_requirements)
    assert all(item["mandatory_status"] == "uncertain" for item in candidate.atomic_requirements)
    assert all(item["ambiguity"] == "open_enumeration" for item in candidate.atomic_requirements)


def test_open_enumeration_explicitly_all_required_is_mandatory():
    candidate = run_text("1、包括但不限于A、B、C，且均须具备。").candidates[0]
    assert candidate.review_required is False
    assert [item["original_text"] for item in candidate.atomic_requirements] == [
        "包括但不限于A",
        "B",
        "C",
    ]
    assert all(item["is_mandatory"] is True for item in candidate.atomic_requirements)
    assert all(item["mandatory_status"] == "required" for item in candidate.atomic_requirements)
    assert_anchors(candidate)


def test_open_enumeration_example_items_keep_example_relation():
    candidate = run_text("1、包括但不限于A、B、C，例如D。").candidates[0]
    assert all(item["is_mandatory"] is False for item in candidate.atomic_requirements)
    assert all(item["example_relation"] is True for item in candidate.atomic_requirements)
    assert all(item["open_enumeration_mode"] == "example" for item in candidate.atomic_requirements)
