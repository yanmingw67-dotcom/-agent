"""第一轮审核反例；原98项测试保持原文不变。"""

import pytest
from sqlalchemy import select

from app.db.models import KnowledgeJob, Snapshot
from app.db.session import session_factory
from app.jobs.handlers import mark_failed
from app.jobs.queue import claim_pending
from tests.conftest import ADMIN, make_zip
from tests.integration.test_import_api import category, execute, upload
from tests.integration.test_p12_index_retrieval import indexed, run_job, tool


def pages(client, cid, sid, name, **kwargs):
    cursor = 0
    collected, responses = [], []
    for _ in range(30):
        response = tool(client, cid, sid, name, cursor=cursor, limit=30, **kwargs)
        assert response.status_code == 200, response.text
        page = response.json()
        collected.extend(page["results"])
        responses.append(page)
        if not page["truncated"]:
            assert page["next_cursor"] is None
            return collected, responses
        assert int(page["next_cursor"]) > cursor
        cursor = int(page["next_cursor"])
    raise AssertionError("分页没有结束")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
@pytest.mark.parametrize("file_count", [1, 3])
def test_r01_all_120_matching_lines_per_file(client, encoding, file_count):
    entries = [(f"source_{i}.txt", ("needle\n" * 120).encode(encoding)) for i in range(file_count)]
    cid, sid = indexed(client, data=make_zip(entries))
    result, responses = pages(client, cid, sid, "search_code_batch", query="needle")
    expected = [(path, line) for path, _ in entries for line in range(1, 121)]
    assert [(i["path"], i["start_line"]) for i in result] == expected
    assert len(responses) == file_count * 4
    assert all(p["truncated"] for p in responses[:-1])


def test_r01_context_budget_cursor_advances_only_emitted_items(client, environment):
    cid, sid = indexed(client, data=make_zip([("one.txt", "needle\n" * 120)]))
    object.__setattr__(environment, "retrieval_max_chars", 7)
    result, _ = pages_short(client, cid, sid)
    assert [i["start_line"] for i in result] == list(range(1, 121))


def pages_short(client, cid, sid):
    collected, cursor = [], 0
    for _ in range(150):
        page = tool(client, cid, sid, "search_code_batch", query="needle", cursor=cursor).json()
        collected += page["results"]
        if not page["truncated"]:
            return collected, page
        next_cursor = int(page["next_cursor"])
        assert next_cursor > cursor
        cursor = next_cursor
    # 最后一页恰好用完字符预算时允许一次空页证明穷尽。
    raise AssertionError("上下文分页没有结束")


def test_r01_timeout_is_not_complete(client, environment):
    cid, sid = indexed(client)
    object.__setattr__(environment, "search_timeout_seconds", 1e-12)
    page = tool(client, cid, sid, "search_code_batch", query="never_found").json()
    assert page["truncated"] and page["next_cursor"] == "0"
    assert "search_timeout_partial" in page["warnings"]


def test_r04_references_have_stable_pages_and_no_duplicate(client):
    code = "def target():\n    pass\n" + "target()\n" * 125
    cid, sid = indexed(client, data=make_zip([("calls.py", code)]))
    symbol = tool(client, cid, sid, "search_symbols", query="target").json()["results"][0]
    result, _ = pages(client, cid, sid, "find_references", symbol_id=symbol["symbol_id"])
    assert len(result) == 125 and len({i["id"] for i in result}) == 125
    assert [i["start_line"] for i in result] == list(range(3, 128))


def test_r07_oversized_and_unbuilt_are_explicit_incomplete_coverage(client, environment):
    object.__setattr__(environment, "index_max_file_bytes", 100)
    cid, sid = indexed(client, data=make_zip([("large.txt", "needle" * 200)]))
    page = tool(client, cid, sid, "search_code_batch", query="needle").json()
    assert not page["results"] and page["coverage_incomplete"]
    assert "oversized" in " ".join(page["warnings"])
    cid = category(client, "notbuilt")
    ids = upload(client, cid, make_zip()).json()
    execute(ids["job_id"])
    page = tool(client, cid, ids["snapshot_id"], "search_code_batch", query="anything").json()
    assert page["coverage_incomplete"] and "source_index_not_complete" in page["warnings"]


def test_r02_migration_repairs_legacy_state_without_publishing(client):
    from alembic import command
    from alembic.config import Config

    cid = category(client, "legacy")
    ids = upload(client, cid, make_zip()).json()
    execute(ids["job_id"])
    _, built = indexed(client, "built")
    with session_factory().begin() as db:
        db.get(Snapshot, ids["snapshot_id"]).index_status = "indexed"
        db.get(Snapshot, built).index_status = "failed"
    command.downgrade(Config("alembic.ini"), "0003")
    command.upgrade(Config("alembic.ini"), "head")
    assert state(ids["snapshot_id"]) == "not_indexed" and state(built) == "indexed"


def document_job(client, cid, sid, contents=b"manual"):
    r = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("manual.txt", contents, "text/plain")},
    )
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def state(sid):
    with session_factory()() as db:
        return db.get(Snapshot, sid).index_status


def test_r02_document_does_not_build_source_or_change_complete_status(client):
    cid = category(client, "unbuilt")
    ids = upload(client, cid, make_zip()).json()
    execute(ids["job_id"])
    sid = ids["snapshot_id"]
    assert state(sid) == "not_indexed"
    run_job(document_job(client, cid, sid))
    assert state(sid) == "not_indexed"
    build = client.post(f"/api/v1/snapshots/{sid}/build", headers=ADMIN).json()
    run_job(build["job_id"])
    assert state(sid) == "indexed"
    failed = document_job(client, cid, sid, b"\xff\xfa")
    run_job(failed)
    assert state(sid) == "indexed"
    assert client.post(f"/api/v1/jobs/{failed}/retry", headers=ADMIN).status_code == 202
    assert state(sid) == "indexed"
    run_job(failed)
    assert state(sid) == "indexed"


@pytest.mark.parametrize("failure_path", ["callback", "lost"])
def test_r02_document_worker_failure_never_clobbers_source(client, environment, failure_path):
    from datetime import timedelta

    from app.db.models import now

    cid, sid = indexed(client)
    jid = document_job(client, cid, sid)
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid)
        job.dispatch_token = "test"
        job.status = "running"
        job.started_at = now() - timedelta(hours=2)
    if failure_path == "callback":
        mark_failed(jid, "test", "WORKER_INTERRUPTED")
    else:
        object.__setattr__(environment, "worker_kind", "index")
        claim_pending()
    assert state(sid) == "indexed"
    with session_factory()() as db:
        assert db.scalar(select(KnowledgeJob.status).where(KnowledgeJob.id == jid)) == "failed"
