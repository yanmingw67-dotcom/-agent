"""P1.6 人工合成公开测试资料：不含任何企业真实文档或源码。

所有内容为独立构造的最小样例，用于结构恢复、噪声列过滤、单源评审与发布测试。
"""

import io
import zipfile

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook


def _write_docx(document, path):
    document.save(path)
    return path.read_bytes()


def docx_manual(revision="v1"):
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading("合成 WAF 技术手册（公开样例）", level=1)
    doc.add_paragraph("本手册用于自动化测试，全部内容为人工合成，不来自任何真实企业资料。")
    doc.add_heading("产品功能", level=2)
    doc.add_paragraph("当前版本支持透明桥、透明代理、反向代理和旁路镜像四种部署接入模式。")
    doc.add_paragraph("支持多租户策略隔离，租户间策略配置相互独立。")
    if revision == "v2":
        doc.add_paragraph("当前版本支持策略模板导入，模板由管理员维护。")
    table = doc.add_table(rows=1, cols=5)
    headers = ["功能分类", "功能名称", "技术参数", "证明材料", "友商对比"]
    for cell, value in zip(table.rows[0].cells, headers, strict=True):
        cell.text = value
    rows = [
        [
            "部署能力",
            "多模式部署",
            "支持透明桥、透明代理、反向代理和旁路镜像接入",
            "需提供截图",
            "支持",
        ],
        ["部署能力", "高可用部署", "支持双机热备部署，最大并发 10000", "检测报告", "支持"],
        ["访问控制", "多租户隔离", "支持多租户策略隔离，租户策略独立", "需提供承诺函", "部分支持"],
        ["访问控制", "规则同步", "支持规则同步至集群各节点", "-", "无"],
        ["访问控制", "规则同步", "不支持规则同步至旧版本节点", "-", "无"],
    ]
    for values in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, values, strict=True):
            cell.text = value
    table.cell(2, 0).merge(table.cell(4, 0))
    doc.add_heading("后续版本规划", level=2)
    doc.add_paragraph("计划支持规则原子同步与零丢失，具体版本待定。")
    doc.add_heading("商标与版权声明", level=2)
    doc.add_paragraph("本手册版权归合成样例项目所有，未经许可不得转载。")
    with NamedTemporaryFile(suffix=".docx", delete=False) as handle:
        handle.close()
        path = Path(handle.name)
    try:
        doc.save(path)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def xlsx_capabilities():
    book = Workbook()
    main = book.active
    main.title = "功能参数"
    main.append(["功能分类", "功能名称", "技术参数", "证明材料", "友商对比"])
    main.append(
        ["访问控制", "多租户策略隔离", "支持多租户策略隔离，租户间策略独立", "需提供承诺函", "支持"]
    )
    main.append(["访问控制", "规则同步", "支持规则同步至集群各节点", "截图", "无"])
    main.append(["访问控制", "攻击防护", "支持 SQL 注入与 XSS 攻击检测和阻断", "检测报告", "支持"])
    main.append(["高可用", "双机热备", "支持双机热备部署，最大并发 10000", "证书", "支持"])
    main.append(["未来规划", "智能编排", "计划支持智能策略编排", "-", "无"])
    main.append(["商务服务", "售后服务", "提供 7x24 小时售后服务", "-", "无"])
    main.merge_cells("A2:A4")
    plan = book.create_sheet("版本规划")
    plan.append(["功能名称", "计划"])
    plan.append(["语义检索", "计划支持，尚未发布"])
    proof = book.create_sheet("证明材料")
    proof.append(["项目", "要求"])
    proof.append(["截图", "投标人需提供界面截图并加盖公章"])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def pdf_document(pages: list[str] | None = None):
    """生成可选中文本的多页最小 PDF；不依赖外部字体或网络。"""
    pages = pages or [
        "Synthetic WAF technical manual. Current product supports transparent proxy and reverse proxy deployment.",
        "Access control: supports multi-tenant policy isolation with independent tenant policies.",
    ]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = []
    next_id = 3
    contents = []
    font_id = 3 + len(pages) * 2
    for text in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 11 Tf 36 760 Td ({escaped}) Tj ET".encode()
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


def markdown_document():
    return """# 合成 WAF 功能说明

## 当前能力

- 支持透明桥接入模式
- 支持多租户策略隔离

## 使用条件

支持集群部署，但需要共享配置中心。

## 后续规划

计划支持语义化策略推荐。

## 商标声明

本文件版权归合成样例项目所有。
""".encode()


def source_archive():
    files = {
        "proxy/deploy.py": (
            "def add_route(app):\n"
            "    app.add_route('/proxy', transparent_proxy)\n"
            "\n"
            "def transparent_proxy(request):\n"
            "    return forward(request)\n"
            "\n"
            "def forward(request):\n"
            "    return request.path\n"
        ),
        "tenant/policy.py": (
            "def isolate_policy(tenant_id, policy):\n"
            "    return {'tenant': tenant_id, 'policy': policy}\n"
            "\n"
            "def load_policy(store, tenant_id):\n"
            "    return store.get(tenant_id)\n"
        ),
        "README.md": "# 合成样例源码\n仅用于自动化测试。\n",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()
