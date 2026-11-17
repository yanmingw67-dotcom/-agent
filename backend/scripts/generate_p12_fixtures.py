"""P1.2 人工公开、跨模块源码和真实文档格式测试资产。"""

import io
import zipfile
from pathlib import Path

from docx import Document
from openpyxl import Workbook


def source_zip(extra_files=0):
    files = {
        "notify/sender.py": "class RobotSender:\n    def send(self, event):\n        return format_event(event)\n\ndef format_event(event):\n    return str(event)\n",
        "api/routes.py": "from notify.sender import RobotSender\n\ndef handle_alert(event):\n    sender = RobotSender()\n    return sender.send(event)\n",
        "transport/http.go": "package transport\n\ntype Sender interface { Send(text string) error }\ntype Robot struct {}\nfunc (r Robot) Send(text string) error { return nil }\nfunc Format(text string) string { return text }\n",
        "config/notify.yaml": "notification:\n  platform: feishu\n",
        "legacy/unknown.rb": "def unknown_feature\n  'unsupported grammar'\nend\n",
        "encoding/bad.py": b"\xff\xfebad odd",
        "tests/test_sender.py": "def test_only():\n    return True\n",
    }
    for index in range(extra_files):
        # 每文件独立真实语法；无需执行，显式用于索引规模基准。
        files[f"generated_module_{index % 20}/source_{index:05d}.py"] = (
            f"class Handler{index}:\n    def handle_event(self, event):\n        return normalize{index}(event)\n\n"
            f"def normalize{index}(event):\n    return str(event)\n"
            + "\n".join(f"# documented behavior line {line} module {index}" for line in range(80))
        )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


def pdf_bytes(text="Robot notification manual"):
    # 最小有效可选中文本 PDF；字体为标准 Helvetica，无网络或模型。
    stream = f"BT /F1 12 Tf 40 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return data


def main():
    root = Path("tests/fixtures/p12")
    root.mkdir(parents=True, exist_ok=True)
    (root / "cross_module_source.zip").write_bytes(source_zip())
    doc = Document()
    doc.add_heading("Notification settings", level=1)
    doc.add_paragraph("Robot notification supports platform-specific payloads.")
    table = doc.add_table(rows=2, cols=2)
    for cell, value in zip(
        [c for r in table.rows for c in r.cells],
        ["Setting", "Value", "Platform", "Feishu"],
        strict=True,
    ):
        cell.text = value
    doc.save(root / "demo_waf_manual.docx")
    book = Workbook()
    sheet = book.active
    sheet.title = "Capabilities"
    sheet["A1"] = "Robot notification"
    sheet.merge_cells("A1:B1")
    sheet["A2"] = "Formula remains source text"
    sheet["B2"] = "=1+1"
    book.save(root / "demo_capabilities.xlsx")
    (root / "demo_waf_manual.pdf").write_bytes(pdf_bytes())
    print("已生成 P1.2 公开人工跨模块源码与 DOCX/PDF/XLSX 测试资产")


if __name__ == "__main__":
    main()
