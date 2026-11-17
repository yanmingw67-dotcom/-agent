"""第一轮整改真实六服务验收，不使用数据库/队列/解析器Mock。"""

import io
import json
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from tests.p12_remediation_assets import many_merges_xlsx, mixed_pdf

ROOT = Path("reports/p12-remediation")


def main():
    env = dotenv_values(".env")
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    api = httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=20)
    retrieval = httpx.Client(base_url="http://127.0.0.1:8001", headers=headers, timeout=20)
    report = {"mode": "REAL_POSTGRES_REDIS_RQ_RETRIEVAL", "checks": {}, "pages": []}
    response = api.post(
        "/api/v1/categories", json={"code": "fix-" + uuid4().hex[:10], "name": "P1.2 整改人工资产"}
    )
    response.raise_for_status()
    cid = response.json()["id"]

    def wait(jid):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            r = api.get(f"/api/v1/jobs/{jid}")
            r.raise_for_status()
            job = r.json()
            if job["status"] in {"succeeded", "partial", "failed"}:
                return job
            time.sleep(0.2)
        raise AssertionError("真实任务超时")

    archive = io.BytesIO()
    entries = {
        "a.txt": ("needle\n" * 120).encode(),
        "b.txt": ("needle\n" * 120).encode("utf-16"),
        "calls.py": ("def target():\n    pass\n" + "target()\n" * 125).encode(),
    }
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    r = api.post(
        f"/api/v1/categories/{cid}/source",
        files={"file": ("source.zip", archive.getvalue(), "application/zip")},
    )
    assert r.status_code == 202, r.text
    sid = r.json()["snapshot_id"]
    assert wait(r.json()["job_id"])["status"] == "succeeded"
    report.update(category_id=cid, snapshot_id=sid)

    def document(filename, data, mime):
        r = api.post(
            f"/api/v1/categories/{cid}/documents",
            data={"snapshot_id": sid},
            files={"file": (filename, data, mime)},
        )
        assert r.status_code == 202, r.text
        job = wait(r.json()["job_id"])
        return r.json(), job

    _, docjob = document("before.txt", b"manual before build", "text/plain")
    assert docjob["status"] == "succeeded"
    before = retrieval.post(
        f"/api/v1/retrieval/{cid}",
        params={"snapshot_id": sid},
        json={"tool": "search_code_batch", "query": "needle"},
    ).json()
    assert before["coverage_incomplete"] and "source_index_not_complete" in before["warnings"]
    report["checks"]["document_before_source_not_indexed"] = True
    r = api.post(f"/api/v1/snapshots/{sid}/build")
    assert r.status_code == 202, r.text
    report["source_job"] = wait(r.json()["job_id"])
    assert report["source_job"]["status"] == "succeeded"

    def tool(name, **kwargs):
        r = retrieval.post(
            f"/api/v1/retrieval/{cid}", params={"snapshot_id": sid}, json={"tool": name, **kwargs}
        )
        assert r.status_code == 200, r.text
        return r.json()

    def all_pages(name, **kwargs):
        cursor = 0
        result = []
        for _ in range(30):
            page = tool(name, limit=30, cursor=cursor, **kwargs)
            report["pages"].append({"tool": name, **page})
            result.extend(page["results"])
            if not page["truncated"]:
                return result
            new = int(page["next_cursor"])
            assert new > cursor
            cursor = new
        raise AssertionError("分页无法穷尽")

    hits = all_pages("search_code_batch", query="needle")
    assert [(i["path"], i["start_line"]) for i in hits] == [
        (p, line_no) for p in ["a.txt", "b.txt"] for line_no in range(1, 121)
    ]
    report["checks"]["r01_240_utf8_utf16_lines_complete"] = True
    sym = tool("search_symbols", query="target")["results"][0]
    refs = all_pages("find_references", symbol_id=sym["symbol_id"])
    assert len(refs) == 125 and len({i["id"] for i in refs}) == 125
    report["checks"]["r04_125_references_complete"] = True
    ids, job = document("mixed.pdf", mixed_pdf(), "application/pdf")
    assert job["status"] == "partial"
    for query, page_no in [("first searchable", 1), ("last searchable", 3)]:
        page = tool("search_documents", query=query)
        assert page["coverage_incomplete"]
        item = page["results"][0]
        assert item["locator"]["page"] == page_no
        assert (
            tool("read_document", segment_id=item["id"])["results"][0]["sha256"] == item["sha256"]
        )
    report["checks"]["r03_mixed_pdf_search_and_read"] = True
    ids, xjob = document(
        "scale.xlsx",
        many_merges_xlsx(1000, 200),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert xjob["status"] == "partial"  # 同快照混合PDF仍partial，但XLSX本身parsed
    docs = api.get(f"/api/v1/snapshots/{sid}/documents").json()
    xdoc = next(d for d in docs if d["id"] == ids["document_id"])
    assert xdoc["parse_status"] == "parsed"
    assert len(xdoc["parse_details"]["sheet_merged_ranges"]["Scale"]) == 200
    report["checks"]["r05_3800_cells_200_merges"] = True
    _, bad = document("bad.txt", b"\xff\xfa", "text/plain")
    assert bad["status"] == "partial"
    after = tool("search_code_batch", query="needle")
    assert "source_index_not_complete" not in after["warnings"]
    report["checks"]["r02_document_fail_keeps_source_indexed"] = True
    report["documents"] = docs
    report["xlsx_job"] = xjob
    report["api_metrics"] = api.get("/api/v1/admin/metrics").json()
    report["retrieval_metrics"] = retrieval.get("/api/v1/admin/metrics").json()
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "remediation-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "pages"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
