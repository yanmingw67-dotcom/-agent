"""P3.1 真实结构回归：表格标题必须结束前一段“编制说明”的噪声章节。"""

import re

from docx import Document

from app.core.config import get_settings
from app.tender.rules import extract
from app.tender.structure import parse_docx


def _document_with_caption(tmp_path):
    path = tmp_path / "caption-after-notice.docx"
    doc = Document()
    doc.add_paragraph("一、编制说明")
    doc.add_paragraph("本表用于说明填写口径，不属于技术参数。")
    doc.add_paragraph("表 1  WAF 技术参数要求")
    table = doc.add_table(rows=1, cols=4)
    for cell, value in zip(
        table.rows[0].cells,
        ["序号", "功能项", "技术参数要求", "证明材料要求"],
        strict=True,
    ):
        cell.text = value
    for index in range(1, 16):
        count = 4 if index == 1 else 3
        technical = "\n".join(
            f"（{part}）支持测试能力{index}-{part}，满足条件。" for part in range(1, count + 1)
        )
        cells = table.add_row().cells
        cells[0].text = str(index)
        cells[1].text = f"测试功能{index}"
        cells[2].text = technical
        cells[3].text = "功能截图"
    doc.add_paragraph("二、服务与商务条款建议")
    doc.add_paragraph("（1）提供三年升级服务。")
    doc.save(path)
    return path


def test_table_caption_starts_a_new_section_after_notice(tmp_path):
    result = extract(parse_docx(_document_with_caption(tmp_path), get_settings()))

    assert len(result.candidates) == 15
    assert [len(item.logic_tree["children"]) for item in result.candidates] == [4] + [3] * 14
    assert (
        sum(len(re.findall(r"[（(]\d+[）)]", item.original_text)) for item in result.candidates)
        == 46
    )
    assert all(
        item.source_locator.get("section") == "表 1  WAF 技术参数要求" for item in result.candidates
    )
    assert all("三年升级服务" not in item.original_text for item in result.candidates)
    assert result.coverage_status == "complete"
