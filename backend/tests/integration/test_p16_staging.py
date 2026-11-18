"""P1.6 暂存派生快照：物理复制、索引、替换/移除与发布隔离。"""

from uuid import uuid4

from sqlalchemy import select

from app.db.index_models import Document, DocumentSegment, SourceFile
from app.db.models import Category, KnowledgeJob, Snapshot
from app.db.session import session_factory
from app.ingestion.indexer import run_index
from app.ingestion.text import digest
from tests import p16_assets
from tests.conftest import ADMIN, READ
from tests.integration.test_p12_index_retrieval import indexed
from tests.integration.test_p16_discovery import (
    discover,
    index_document,
    upload_document,
)


def setup(client):
    return indexed(
        client,
        code="p16s-" + uuid4().hex[:8],
        data=p16_assets.source_archive(),
    )


def set_active(cid, sid):
    with session_factory().begin() as db:
        db.get(Category, cid).active_snapshot_id = sid


def derive(client, cid):
    response = client.post(f"/api/v1/categories/{cid}/document-staging-snapshots", headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def build(client, sid):
    response = client.post(f"/api/v1/snapshots/{sid}/build", headers=ADMIN)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, job_id).dispatch_token = "p16-staging"
    run_index(job_id, "p16-staging")
    with session_factory()() as db:
        job = db.get(KnowledgeJob, job_id)
        assert job.status in {"succeeded", "partial"}, job.error_code
        assert db.get(Snapshot, sid).index_status == "indexed"


def document(client, cid, sid, name, data, mime):
    return upload_document(client, cid, sid, name, data, mime)


def test_derive_snapshot_copies_source_and_documents_physically(client, environment):
    cid, sid = setup(client)
    index_document(
        document(
            client,
            cid,
            sid,
            "manual.docx",
            p16_assets.docx_manual(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )
    set_active(cid, sid)
    with session_factory()() as db:
        source_docs = list(db.scalars(select(Document).where(Document.snapshot_id == sid)))
    result = derive(client, cid)
    new_sid = result["snapshot_id"]
    assert new_sid != sid
    assert result["derived_from_snapshot_id"] == sid
    assert result["derivation_type"] == "document_update"
    assert result["documents_copied"] == len(source_docs)
    with session_factory()() as db:
        new = db.get(Snapshot, new_sid)
        docs = list(db.scalars(select(Document).where(Document.snapshot_id == new_sid)))
        assert new.status == "extracted" and new.index_status == "not_indexed"
        assert new.source_sha256 == db.get(Snapshot, sid).source_sha256
        assert len(docs) == len(source_docs)
        for old, copied in zip(source_docs, docs, strict=True):
            assert copied.id != old.id
            assert copied.storage_key != old.storage_key
            assert copied.sha256 == old.sha256
            assert copied.origin_json["source_document_id"] == old.id
            old_path = environment.document_store / old.storage_key
            new_path = environment.document_store / copied.storage_key
            assert old_path.read_bytes() == new_path.read_bytes()
            assert copied.origin_json["derived_from_snapshot_id"] == sid
        assert (environment.source_root / new_sid / "manifest.json").read_text() == (
            environment.source_root / sid / "manifest.json"
        ).read_text().replace(sid, new_sid)
    assert (environment.archive_root / new.archive_key).is_file()
    assert not list(db.scalars(select(SourceFile).where(SourceFile.snapshot_id == new_sid)))


def test_build_derived_snapshot_and_replace_remove_document(client, environment):
    cid, sid = setup(client)
    index_document(
        document(
            client,
            cid,
            sid,
            "manual.docx",
            p16_assets.docx_manual(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )
    index_document(
        document(
            client,
            cid,
            sid,
            "matrix.xlsx",
            p16_assets.xlsx_capabilities(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    )
    set_active(cid, sid)
    new_sid = derive(client, cid)["snapshot_id"]
    build(client, new_sid)
    with session_factory()() as db:
        docs = list(db.scalars(select(Document).where(Document.snapshot_id == new_sid)))
        assert len(docs) == 2
        assert all(
            db.scalar(select(DocumentSegment.id).where(DocumentSegment.document_id == doc.id))
            for doc in docs
        )
    docx = next(doc for doc in docs if doc.filename.endswith(".docx"))
    replacement = p16_assets.docx_manual(revision="v2")
    response = client.post(
        f"/api/v1/snapshots/{new_sid}/documents/{docx.id}/replace",
        headers=ADMIN,
        files={
            "file": (
                "manual.docx",
                replacement,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, job_id).dispatch_token = "p16-replace"
    run_index(job_id, "p16-replace")
    with session_factory()() as db:
        updated = db.get(Document, docx.id)
        assert updated.sha256 != docx.sha256
        assert updated.parse_status == "parsed"
        assert updated.origin_json["replaced"] is True
        assert updated.origin_json["previous_storage_key_sha256"] == docx.sha256
        assert not (environment.document_store / docx.storage_key).exists()
    xlsx = next(doc for doc in docs if doc.filename.endswith(".xlsx"))
    removed = client.delete(f"/api/v1/snapshots/{new_sid}/documents/{xlsx.id}", headers=ADMIN)
    assert removed.status_code == 200, removed.text
    assert not (environment.document_store / xlsx.storage_key).exists()
    with session_factory()() as db:
        assert db.get(Document, xlsx.id) is None
        assert not db.scalar(
            select(DocumentSegment.id).where(DocumentSegment.document_id == xlsx.id)
        )


def test_published_snapshot_documents_cannot_be_modified(client):
    cid, sid = setup(client)
    with session_factory().begin() as db:
        db.get(Snapshot, sid).status = "published"
    response = client.delete(f"/api/v1/snapshots/{sid}/documents/any", headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["error_code"] == "DOCUMENT_STAGING_REQUIRED"
    assert (
        client.post(
            f"/api/v1/categories/{cid}/document-staging-snapshots", headers=ADMIN
        ).status_code
        == 409
    )


def probe(client, cid, text):
    return client.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": cid, "requirement_text": text},
    )


def test_document_update_isolated_until_publish_and_rollback(client, environment):
    from tests.integration.test_p16_discovery import document_review, draft_claim

    object.__setattr__(environment, "service_mode", "retrieval")
    cid, sid = setup(client)
    index_document(
        document(
            client,
            cid,
            sid,
            "manual.docx",
            p16_assets.docx_manual(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )
    discover(client, sid)
    claim = draft_claim(sid, "多租户隔离")
    revision = client.post(
        f"/api/v1/capabilities/{claim.id}/review",
        headers=ADMIN,
        json=document_review(claim, name="多租户隔离"),
    )
    assert revision.status_code == 200, revision.text
    pid1_resp = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision.json()["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "发布初始文档单源能力",
        },
    )
    assert pid1_resp.status_code == 200, pid1_resp.text
    pid1 = pid1_resp.json()["publication_id"]
    before = client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
    old_probe = probe(client, cid, "模板导入")
    assert old_probe.status_code == 200
    assert old_probe.json()["candidates"] == []

    new_sid = derive(client, cid)["snapshot_id"]
    build(client, new_sid)
    with session_factory()() as db:
        docx = db.scalar(
            select(Document).where(
                Document.snapshot_id == new_sid, Document.filename == "manual.docx"
            )
        )
        old_docx = db.scalar(
            select(Document).where(Document.snapshot_id == sid, Document.filename == "manual.docx")
        )
        old_key, old_sha = old_docx.storage_key, old_docx.sha256
        old_bytes = (environment.document_store / old_key).read_bytes()
    replacement = p16_assets.docx_manual(revision="v2")
    replaced = client.post(
        f"/api/v1/snapshots/{new_sid}/documents/{docx.id}/replace",
        headers=ADMIN,
        files={
            "file": (
                "manual.docx",
                replacement,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert replaced.status_code == 202, replaced.text
    with session_factory().begin() as db:
        db.get(KnowledgeJob, replaced.json()["job_id"]).dispatch_token = "p16-replace2"
    run_index(replaced.json()["job_id"], "p16-replace2")

    # 新暂存文档只存在于派生快照，旧发布与旧物理文件保持不变。
    with session_factory()() as db:
        assert db.get(Document, old_docx.id).sha256 == old_sha
    assert (environment.document_store / old_key).read_bytes() == old_bytes
    unchanged = client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
    assert unchanged == before
    assert probe(client, cid, "模板导入").json()["candidates"] == []

    discover(client, new_sid)
    new_claim = draft_claim(new_sid, "当前版本支持策略模板导入，模板由管理员维护。")
    assert new_claim is not None
    new_revision = client.post(
        f"/api/v1/capabilities/{new_claim.id}/review",
        headers=ADMIN,
        json=document_review(new_claim, name="当前版本支持策略模板导入，模板由管理员维护。"),
    )
    assert new_revision.status_code == 200, new_revision.text
    pid2_resp = client.post(
        f"/api/v1/snapshots/{new_sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [new_revision.json()["id"]],
            "expected_active_publication_id": pid1,
            "acknowledge_coverage_gaps": True,
            "comment": "发布文档更新后的暂存快照",
        },
    )
    assert pid2_resp.status_code == 200, pid2_resp.text
    pid2 = pid2_resp.json()["publication_id"]
    assert pid2 != pid1
    new_probe = probe(client, cid, "模板导入")
    assert new_probe.status_code == 200
    assert (
        new_probe.json()["candidates"][0]["content"]["name"]
        == "当前版本支持策略模板导入，模板由管理员维护。"
    )

    rolled = client.post(
        f"/api/v1/categories/{cid}/rollback",
        headers=ADMIN,
        json={
            "publication_id": pid1,
            "expected_active_publication_id": pid2,
            "comment": "回滚验证旧清单与证据精确复原",
        },
    )
    assert rolled.status_code == 200, rolled.text
    restored = client.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()
    assert restored == before
    assert probe(client, cid, "模板导入").json()["candidates"] == []
    old_again = probe(client, cid, "多租户策略隔离")
    assert old_again.json()["candidates"][0]["content"]["name"] == "多租户隔离"
    assert all(item["kind"] == "document" for item in old_again.json()["evidence"])
    evidence = old_again.json()["evidence"][0]
    assert evidence["source_ref"]["document_id"]
    assert evidence["source_ref"]["segment_id"]
    assert evidence["locator"]
    assert evidence["quote"]
    assert evidence["quote_hash"] == digest(evidence["quote"].encode())
