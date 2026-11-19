"""P2.1 人工合成招标样例。

全部内容为测试构造，不含任何真实企业招标文件；真实 WAF 文件不可用时，
测试报告必须明确区分合成样例与真实附件。
"""

import io

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook

PARAM_NAMES = [
    "Web攻击防护",
    "SQL注入防护",
    "XSS防护",
    "命令注入防护",
    "漏洞防护",
    "协议解析",
    "访问控制",
    "身份认证",
    "日志审计",
    "告警联动",
    "多租户隔离",
    "高可用部署",
    "双机热备",
    "性能吞吐",
    "集中管理",
]


def technical_text(name: str, index: int):
    return (
        f"（1）支持{name}能力{index}A；\n"
        f"（2）支持{name}能力{index}B；\n"
        f"（3）支持{name}能力{index}C。"
    )


def waf_docx(*, merged: bool = False) -> bytes:
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    section = doc.sections[0]
    section.header.paragraphs[0].text = "合成 WAF 招标文件"
    section.footer.paragraphs[0].text = "第 1 页 共 1 页"
    doc.add_heading("第一章 编制说明", level=1)
    doc.add_paragraph("本表由投标人填写，目录、页码和填写说明不构成技术参数。")
    doc.add_paragraph("投标人应按要求提供检测报告、功能截图和承诺函。")
    doc.add_heading("第二章 技术参数表", level=1)
    table = doc.add_table(rows=1, cols=4)
    headers = ["序号", "功能项", "技术参数", "证明材料"]
    for cell, value in zip(table.rows[0].cells, headers, strict=True):
        cell.text = value
    for index, name in enumerate(PARAM_NAMES, 1):
        cells = table.add_row().cells
        cells[0].text = str(index)
        cells[1].text = name
        cells[2].text = technical_text(name, index)
        cells[3].text = "提供检测报告及功能截图"
        if index == 8:
            repeat = table.add_row().cells
            for cell, value in zip(repeat, headers, strict=True):
                cell.text = value
    if merged:
        table.cell(1, 0).merge(table.cell(3, 0))
        extra = table.add_row().cells
        extra[0].text = ""
        extra[1].text = ""
        extra[2].text = "（4）支持Web攻击防护能力1D。"
        extra[3].text = ""
    doc.add_heading("第三章 服务与商务条款", level=1)
    doc.add_paragraph("提供五年原厂售后服务，报价与付款方式见商务文件。")
    doc.add_paragraph("友商产品对比不作为技术参数。")
    doc.add_heading("第四章 编制注意事项", level=1)
    doc.add_paragraph("本表说明仅供填写参考，投标人不得修改表头。")
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def waf_xlsx() -> bytes:
    book = Workbook()
    main = book.active
    main.title = "技术参数"
    main.append(["功能分类", "功能名称", "技术参数", "证明材料", "备注"])
    rows = [
        [
            "攻击防护",
            "Web攻击防护",
            "（1）支持 SQL 注入检测；\n（2）支持 XSS 检测。",
            "检测报告",
            "无",
        ],
        [
            "攻击防护",
            "命令注入防护",
            "（1）支持命令注入检测；\n（2）支持规则自定义。",
            "截图",
            "无",
        ],
        ["攻击防护", "漏洞防护", "（1）支持虚拟补丁；\n（2）支持漏洞库更新。", "证书", "无"],
        ["访问控制", "身份认证", "（1）支持本地认证；\n（2）支持 LDAP 认证。", "承诺函", "无"],
        ["集中管理", "日志审计", "（1）支持日志检索；\n（2）支持日志导出 PDF。", "无", "无"],
    ]
    for row in rows:
        main.append(row)
    main.merge_cells("A2:A4")
    service = book.create_sheet("服务与商务")
    service.append(["项目", "要求"])
    service.append(["售后服务", "提供五年原厂服务"])
    notes = book.create_sheet("填写说明")
    notes.append(["说明"])
    notes.append(["本表由投标人填写"])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def five_text():
    return """技术参数要求
1、支持 HTTP/1.1、HTTP/2 和 WebSocket 协议解析。
2、支持 SQL 注入、XSS、命令注入等攻击检测与阻断。
3、支持日志导出 PDF。
4、支持多租户策略隔离。
5、支持双机热备部署。
"""


def unnumbered_text():
    return """技术参数要求
支持 HTTP/1.1 协议解析。
支持 SQL 注入检测。
支持日志导出 PDF。
支持多租户隔离。
支持告警联动。
"""


def compound_text():
    return """1、支持 Web 攻击防护，具体要求如下：
（1）支持 SQL 注入检测；
（2）支持 XSS 检测；
（3）支持命令注入检测。
"""


def pdf_pages():
    return [
        "Synthetic WAF tender technical parameters.\n"
        "1. Support HTTP/1.1 and HTTP/2 protocol parsing.\n"
        "2. Support SQL injection detection.\n"
        "3. Support log export in PDF format.",
        "4. Support multi-tenant policy isolation.\n5. Support dual-node hot standby deployment.",
    ]


def _pdf_escape(value: str):
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_lines_document(pages: list[str]):
    """生成每个换行都是独立文本行的最小 PDF；用于跨页表/扫描缺口测试。"""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = []
    next_id = 3
    contents = []
    font_id = 3 + len(pages) * 2
    for text in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        lines = text.splitlines() or [""]
        stream = b"BT /F1 11 Tf 36 760 Td "
        for line in lines:
            stream += f"({_pdf_escape(line)}) Tj 0 -18 Td ".encode()
        stream += b"ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        contents.append(content_id)
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.insert(1, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
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
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return data


def pdf_with_empty_page():
    return pdf_lines_document(
        ["1. Support HTTP protocol parsing.", "", "2. Support SQL injection detection."]
    )
