import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.security import AppError
from app.db.index_models import CodeSymbol, Document, DocumentSegment, SourceFile
from app.db.models import Category, KnowledgeJob
from app.db.session import session_factory
from app.ingestion.capacity import ledger, release, reserve
from app.ingestion.doc_parsers import DocumentError, parse_document
from app.ingestion.indexer import run_index
from app.ingestion.language_adapters import analyze
from app.retrieval.schemas import RunContext
from app.retrieval.tools import ReadOnlyTools
from scripts.generate_p12_fixtures import pdf_bytes, source_zip
from tests.conftest import ADMIN, READ, make_zip
from tests.integration.test_import_api import category, execute, upload


def indexed(client, code="p12", data=None):
    cid = category(client, code)
    result = upload(client, cid, data or source_zip()).json()
    execute(result["job_id"])
    response = client.post(f"/api/v1/snapshots/{result['snapshot_id']}/build", headers=ADMIN)
    assert response.status_code == 202, response.text
    run_job(response.json()["job_id"])
    return cid, result["snapshot_id"]


def run_job(jid):
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.dispatch_token = "p12-test"
    run_index(jid, "p12-test")
    with session_factory()() as db:
        job = db.get(KnowledgeJob, jid)
        assert job.status in {"succeeded", "partial"}, job.error_code
        return job.metrics_json


def tool(client, cid, sid, name, **kwargs):
    return client.post(
        f"/api/v1/retrieval/{cid}",
        params={"snapshot_id": sid},
        headers=ADMIN,
        json={"tool": name, **kwargs},
    )


def test_two_languages_symbols_and_exact_cross_module_read(client):
    cid, sid = indexed(client)
    result = tool(client, cid, sid, "search_symbols", query="RobotSender.send")
    assert result.status_code == 200, result.text
    item = result.json()["results"][0]
    assert item["kind"] == "method" and item["start_line"] == 2 and item["end_line"] == 3
    read = tool(client, cid, sid, "read_source", symbol_id=item["symbol_id"]).json()
    assert (
        read["results"][0]["snippet"]
        == "    def send(self, event):\n        return format_event(event)"
    )
    assert read["results"][0]["sha256"] == item["sha256"]
    go = tool(client, cid, sid, "search_symbols", query="Sender").json()["results"]
    assert any(x["kind"] == "interface" for x in go)
    refs = tool(client, cid, sid, "find_references", symbol_id=item["symbol_id"]).json()
    assert any(x["path"] == "api/routes.py" and x["unresolved"] for x in refs["results"])
    assert tool(client, cid, sid, "get_project_map").json()["results"]
    assert tool(client, cid, sid, "list_entrypoints").json()["results"]


def test_incremental_checkpoints_are_stable(client):
    _, sid = indexed(client)
    with session_factory()() as db:
        before = set(db.scalars(select(CodeSymbol.id).where(CodeSymbol.snapshot_id == sid)))
        files = db.scalar(
            select(func.count()).select_from(SourceFile).where(SourceFile.snapshot_id == sid)
        )
    build = client.post(f"/api/v1/snapshots/{sid}/build", headers=ADMIN)
    metrics = run_job(build.json()["job_id"])
    assert metrics["indexed_files"] == 0 and metrics["reused_files"] == files
    with session_factory()() as db:
        assert before == set(db.scalars(select(CodeSymbol.id).where(CodeSymbol.snapshot_id == sid)))


def test_product_and_snapshot_isolation_and_read_role(client):
    cid, sid = indexed(client, "waf")
    other, other_sid = indexed(client, "ndr")
    symbol = tool(client, other, other_sid, "search_symbols", query="format_event").json()[
        "results"
    ][0]
    assert tool(client, cid, sid, "read_source", symbol_id=symbol["symbol_id"]).status_code == 404
    assert tool(client, cid, other_sid, "get_project_map").status_code == 404
    assert (
        client.post(
            f"/api/v1/retrieval/{cid}", headers=READ, json={"tool": "get_project_map"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/retrieval/{cid}",
            params={"snapshot_id": sid},
            headers=READ,
            json={"tool": "get_project_map"},
        ).status_code
        == 403
    )
    with session_factory()() as db:
        with pytest.raises(AppError):
            ReadOnlyTools(
                db,
                RunContext(
                    category_id=cid,
                    snapshot_id=other_sid,
                    principal_id="admin",
                    policy="admin_staging",
                ),
            )
        assert db.get(Category, cid).active_snapshot_id is None


def test_ripgrep_literal_regex_path_and_context_limits(client, environment):
    cid, sid = indexed(client)
    result = tool(client, cid, sid, "search_code_batch", queries=["sender.send", "format_event"])
    assert result.status_code == 200, result.text
    assert any(
        i["path"] == "api/routes.py" and i["start_line"] == 5 for i in result.json()["results"]
    )
    assert tool(client, cid, sid, "search_code_batch", query="Robot.*", mode="regex").json()[
        "results"
    ]
    assert tool(client, cid, sid, "search_code_batch", query="(", mode="regex").status_code == 422
    file = tool(client, cid, sid, "search_paths", query="sender.py").json()["results"][0]
    object.__setattr__(environment, "read_max_lines", 1)
    read = tool(
        client, cid, sid, "read_source", file_id=file["file_id"], start_line=1, end_line=5
    ).json()
    assert read["truncated"] is True and read["results"][0]["end_line"] == 1
    assert (
        tool(client, cid, sid, "read_source", file_id=file["file_id"], start_line=999).status_code
        == 422
    )


def test_sensitive_sources_and_hash_tamper(client, environment):
    cid, sid = indexed(
        client, data=make_zip([("main.py", "api_key = 'not_a_real_secret_value'\n")])
    )
    assert tool(client, cid, sid, "search_code_batch", query="api_key").json()["results"] == []
    with session_factory()() as db:
        f = db.scalar(select(SourceFile).where(SourceFile.snapshot_id == sid))
        assert f.sensitive
        fid = f.id
    assert tool(client, cid, sid, "read_source", file_id=fid).status_code == 403
    cid2, sid2 = indexed(client, "clean")
    item = tool(client, cid2, sid2, "search_paths", query="sender.py").json()["results"][0]
    path = environment.source_root / sid2 / "source/notify/sender.py"
    path.chmod(0o640)
    path.write_text("tampered")
    assert tool(client, cid2, sid2, "read_source", file_id=item["file_id"]).status_code == 409


def test_degraded_language_encoding_size_and_syntax(client, environment):
    object.__setattr__(environment, "index_max_file_bytes", 120)
    _, sid = indexed(
        client,
        data=make_zip(
            [
                ("large.py", "x" * 200),
                ("odd.py", b"\xff\xfa"),
                ("unknown.rb", "def ruby\nend"),
                ("broken.py", "def bad(:\n"),
                ("encoded.txt", "中文".encode("utf-16")),
            ]
        ),
    )
    with session_factory()() as db:
        states = {
            f.path: f.parse_status
            for f in db.scalars(select(SourceFile).where(SourceFile.snapshot_id == sid))
        }
    assert states["large.py"] == "oversized"
    assert states["odd.py"] == "invalid_encoding"
    assert states["unknown.rb"] == "unsupported"
    assert states["broken.py"] == "syntax_error"


@pytest.mark.parametrize(
    "filename,mime,expected_locator",
    [
        (
            "demo_waf_manual.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "paragraph",
        ),
        ("demo_waf_manual.pdf", "application/pdf", "page"),
        (
            "demo_capabilities.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sheet",
        ),
    ],
)
def test_real_documents_traceable_and_readable(client, filename, mime, expected_locator):
    cid, sid = indexed(client)
    data = (Path("tests/fixtures/p12") / filename).read_bytes()
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": (filename, data, mime)},
    )
    assert response.status_code == 202, response.text
    ids = response.json()
    run_job(ids["job_id"])
    result = tool(client, cid, sid, "search_documents", query="").json()
    assert result["results"] and any(expected_locator in i["locator"] for i in result["results"])
    item = result["results"][0]
    reread = tool(client, cid, sid, "read_document", segment_id=item["id"]).json()["results"][0]
    assert reread["snippet"] == item["snippet"] and reread["sha256"] == item["sha256"]
    if filename.endswith(".xlsx"):
        assert any(i["locator"]["merged_ranges"] == ["A1:B1"] for i in result["results"])
        assert any(i["locator"]["formula_not_evaluated"] for i in result["results"])
    with session_factory()() as db:
        assert db.get(Document, ids["document_id"]).parse_status == "parsed"


@pytest.mark.parametrize(
    "filename,data,expected",
    [
        ("bad.docx", b"PK\x03\x04not zip", "failed"),
        ("scan.pdf", pdf_bytes(""), "ocr_required"),
        ("bad.txt", b"\xff\xfa", "failed"),
    ],
)
def test_invalid_documents_visible_status(client, filename, data, expected):
    cid, sid = indexed(client)
    mime = "application/pdf" if filename.endswith(".pdf") else "application/octet-stream"
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": (filename, data, mime)},
    )
    assert response.status_code == 202
    ids = response.json()
    run_job(ids["job_id"])
    with session_factory()() as db:
        doc = db.get(Document, ids["document_id"])
        assert doc.parse_status == expected and doc.error_code
        assert not list(
            db.scalars(select(DocumentSegment).where(DocumentSegment.document_id == doc.id))
        )


def test_capacity_reservation_limit_release_orphan_report(client, environment):
    object.__setattr__(environment, "storage_quota_bytes", 10)
    reserve(environment, "reservation", 8)
    with pytest.raises(AppError):
        reserve(environment, "other", 8)
    release(environment, "reservation")
    with ledger(environment) as state:
        assert state == {}
    environment.archive_root.mkdir(exist_ok=True)
    (environment.archive_root / "orphan.zip").write_bytes(b"abc")
    report = client.get("/api/v1/admin/storage", headers=ADMIN)
    assert report.status_code == 200 and report.json()["automatic_deletion"] is False
    assert any(not e["referenced"] for e in report.json()["entries"])
    assert (environment.archive_root / "orphan.zip").exists()
    assert client.get("/api/v1/admin/storage", headers=READ).status_code == 403


def test_failed_upload_releases_quota(client, environment):
    cid = category(client)
    assert upload(client, cid, b"invalid").status_code == 422
    with ledger(environment) as state:
        assert not state


def test_office_entity_rejected(tmp_path, environment):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE doc [<!ENTITY test "x">]><doc/>')
    path = tmp_path / "evil.docx"
    path.write_bytes(output.getvalue())
    with pytest.raises(DocumentError, match="XML_ENTITY_FORBIDDEN"):
        list(parse_document(path, ".docx", environment))


def test_tree_sitter_nested_symbols_real_grammars():
    status, symbols, _, _ = analyze("class A:\n    def method(self):\n        return 1\n", "python")
    assert status == "parsed" and symbols[1]["qualified_name"] == "A.method"
    status, symbols, _, _ = analyze("package x\ntype I interface { Send() }\n", "go")
    assert status == "parsed" and any(s["kind"] == "interface" for s in symbols)
