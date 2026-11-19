"""P2.3 导出文本与定位格式的纯函数测试。"""

from app.workbench.context import locator_text, source_type_label
from app.workbench.exporter import _cell_text, _text


def test_excel_formula_prefixes_are_escaped():
    for value in [
        '=HYPERLINK("http://example.invalid")',
        "+1+1",
        "-2+3",
        "@SUM(A1:A2)",
        "\t=cmd",
        "\r=cmd",
    ]:
        assert _cell_text(value, 100).startswith("'")
    assert _cell_text("普通参数", 100) == "普通参数"
    assert _cell_text("支持 HTTP/1.1", 100) == "支持 HTTP/1.1"


def test_cell_text_limits_and_removes_control_characters():
    assert _cell_text("a\x00b\x07c", 100) == "abc"
    assert _cell_text("x" * 20, 10) == "x" * 9 + "…"
    assert _text("第一行\r\n第二行", 100) == "第一行\n第二行"


def test_locator_and_source_labels_are_user_readable():
    assert source_type_label("source") == "源码"
    assert source_type_label("document") == "技术文档"
    assert source_type_label("other") == "other"
    assert locator_text({"start_line": 10, "end_line": 20}) == "第 10-20 行"
    assert locator_text({"page": 3, "paragraph": 2}) == "第 3 页 第 2 段"
    assert locator_text({"sheet": "技术参数", "row": 4, "cell": "C4"}) == (
        "工作表 技术参数 第 4 行 单元格 C4"
    )
    assert locator_text({"custom": "x"}) == "custom=x"
