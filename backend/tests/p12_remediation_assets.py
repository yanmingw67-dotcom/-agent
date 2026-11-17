"""公开人工整改资产；只构造文件，不执行被索引源码。"""

import io

from openpyxl import Workbook


def mixed_pdf(pages=("first searchable page", "", "last searchable page")):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        content_id = 5 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        stream = f"BT /F1 12 Tf 40 100 Td ({text}) Tj ET".encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{o:010} 00000 n \n".encode() for o in offsets[1:])
    return (
        data
        + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )


def many_merges_xlsx(rows=1000, merges=200):
    book = Workbook()
    sheet = book.active
    sheet.title = "Scale"
    for row in range(1, rows + 1):
        for column in range(1, 5):
            sheet.cell(row, column, f"cell {row} {column}")
        if row <= merges:
            sheet.merge_cells(start_row=row, end_row=row, start_column=1, end_column=2)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()
