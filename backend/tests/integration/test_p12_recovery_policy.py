from sqlalchemy import select

from app.db.index_models import SourceFile
from app.db.models import KnowledgeJob
from app.db.session import session_factory
from app.ingestion import indexer
from tests.conftest import ADMIN, make_zip
from tests.integration.test_import_api import category, execute, upload
from tests.integration.test_p12_index_retrieval import indexed, run_job, tool


def test_incremental_retry_after_file_commit_reuses_completed_file(client, monkeypatch):
    cid = category(client, "resume")
    ids = upload(
        client,
        cid,
        make_zip([("a.py", "def one():\n    return 1\n"), ("b.py", "def two():\n    return 2\n")]),
    ).json()
    execute(ids["job_id"])
    jid = client.post(f"/api/v1/snapshots/{ids['snapshot_id']}/build", headers=ADMIN).json()[
        "job_id"
    ]
    real = indexer.index_file

    def fail_second(sid, record, settings):
        if record["path"] == "b.py":
            raise RuntimeError("测试故障：第一个文件已提交")
        return real(sid, record, settings)

    monkeypatch.setattr(indexer, "index_file", fail_second)
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "first"
    indexer.run_index(jid, "first")
    with session_factory()() as db:
        assert db.get(KnowledgeJob, jid).status == "failed"
        original = db.scalar(select(SourceFile).where(SourceFile.snapshot_id == ids["snapshot_id"]))
        fid = original.id
    monkeypatch.setattr(indexer, "index_file", real)
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    metrics = run_job(jid)
    assert metrics["reused_files"] == 1 and metrics["indexed_files"] == 1
    with session_factory()() as db:
        assert db.get(SourceFile, fid).path == "a.py"


def test_retrieval_service_hides_write_routes(client, environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    object.__setattr__(environment, "service_mode", "retrieval")
    with TestClient(create_app()) as search:
        assert (
            search.post(
                "/api/v1/categories", headers=ADMIN, json={"code": "x", "name": "x"}
            ).status_code
            == 404
        )
        assert search.get("/api/v1/admin/storage", headers=ADMIN).status_code == 404


def test_strict_tool_scope_fields_and_invalid_limits(client):
    cid, sid = indexed(client)
    response = client.post(
        f"/api/v1/retrieval/{cid}",
        headers=ADMIN,
        params={"snapshot_id": sid},
        json={"tool": "get_project_map", "category_id": "model-override"},
    )
    assert response.status_code == 422
    assert tool(client, cid, sid, "search_symbols", limit=101).status_code == 422
    assert tool(client, cid, sid, "search_code_batch", queries=["x" * 201]).status_code == 422


def test_document_cross_snapshot_segment_ids_are_blocked(client):
    from tests.integration.test_p12_index_retrieval import run_job

    cid, sid = indexed(client, "first")
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("test.txt", b"manual text", "text/plain")},
    )
    run_job(response.json()["job_id"])
    segment = tool(client, cid, sid, "search_documents", query="manual").json()["results"][0]
    other, other_sid = indexed(client, "second")
    assert (
        tool(client, other, other_sid, "read_document", segment_id=segment["id"]).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/categories/{other}/documents",
            headers=ADMIN,
            data={"snapshot_id": sid},
            files={"file": ("test.txt", b"manual text", "text/plain")},
        ).status_code
        == 404
    )
