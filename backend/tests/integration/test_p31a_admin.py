"""P3.1-A 管理员工作台：权限、文件边界、审核发布与审计。"""

from uuid import uuid4

from sqlalchemy import select

from app.db.index_models import Document, DocumentSegment
from app.db.knowledge_models import Evidence
from app.db.models import KnowledgeJob, Snapshot
from app.db.session import session_factory
from app.ingestion.indexer import run_index
from tests.conftest import ADMIN, READ
from tests.integration.test_p14_review_publication import accept, request, setup
from tests.p16_assets import docx_manual, xlsx_capabilities


def error_code(response):
    return response.json().get("error_code")


def load_category(client, cid):
    overview = client.get("/api/v1/admin/overview", headers=ADMIN)
    assert overview.status_code == 200, overview.text
    return next(row for row in overview.json()["categories"] if row["id"] == cid)


def run_job(job_id):
    with session_factory().begin() as db:
        db.get(KnowledgeJob, job_id).dispatch_token = "p31a-doc"
    run_index(job_id, "p31a-doc")


def upload(client, cid, sid, name, data, mime, headers=ADMIN):
    return client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=headers,
        data={"snapshot_id": sid},
        files={"file": (name, data, mime)},
    )


def derived_snapshot(client, cid):
    response = client.post(f"/api/v1/categories/{cid}/document-staging-snapshots", headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()["snapshot_id"]


def test_admin_routes_require_admin_and_publish_limits(client):
    paths = [
        "/api/v1/admin/overview",
        "/api/v1/admin/categories",
        "/api/v1/admin/jobs",
        "/api/v1/admin/audit",
    ]
    for path in paths:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=READ).status_code == 403

    me = client.get("/api/v1/auth/me", headers=ADMIN)
    assert me.status_code == 200, me.text
    assert me.json()["is_admin"] is True
    assert me.json()["role"] == "admin"
    reader = client.get("/api/v1/auth/me", headers=READ)
    assert reader.status_code == 200
    assert reader.json()["is_admin"] is False

    body = client.get("/api/v1/admin/overview", headers=ADMIN).json()
    assert body["model_authorization"]["frontend_can_override"] is False
    assert body["model_authorization"]["server_side_authorization_required"] is True
    assert body["limits"]["document_only_build_supported"] is False
    assert body["limits"]["document_types"] == [".docx", ".pdf", ".xlsx"]


def test_admin_aggregates_only_expose_current_work_without_leaking_other_roles(client):
    cid, sid, claims, evidence = setup(client)
    row = load_category(client, cid)
    assert row["knowledge_ready"] is False
    assert row["latest_snapshot"]["id"] == sid
    assert row["pending_review_count"] == len(claims)

    workspace = client.get(f"/api/v1/admin/categories/{cid}", headers=ADMIN)
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    assert body["working_snapshot"]["id"] == sid
    assert body["capabilities"]["source_required"] is True
    assert body["capabilities"]["can_derive_documents"] is True
    assert body["documents"] == []
    assert body["review"] == {
        "total": 3,
        "accepted": 0,
        "unreviewed": 3,
        "rejected": 0,
        "returned": 0,
        "modified": 0,
        "merged": 0,
        "pending": 3,
    }

    queue = client.get(f"/api/v1/admin/snapshots/{sid}/review-queue", headers=ADMIN)
    assert queue.status_code == 200, queue.text
    assert [item["claim_id"] for item in queue.json()["items"]] == claims
    assert queue.json()["accepted_revision_ids"] == []

    release = client.get(f"/api/v1/admin/snapshots/{sid}/release-check", headers=ADMIN)
    assert release.status_code == 200, release.text
    assert release.json()["publishable"] is False
    assert any("NO_ACCEPTED_REVISION" in item["errors"] for item in release.json()["blocking"])

    jobs = client.get(
        f"/api/v1/admin/jobs?category_id={cid}&job_type=knowledge_build", headers=ADMIN
    )
    assert jobs.status_code == 200
    assert jobs.json()["total"] == 1

    missing = client.get("/api/v1/admin/categories/not-a-category", headers=ADMIN)
    assert missing.status_code == 404
    assert error_code(missing) == "CATEGORY_NOT_FOUND"
    foreign_snapshot = client.get(
        f"/api/v1/admin/categories/{cid}?snapshot_id={uuid4()}", headers=ADMIN
    )
    assert foreign_snapshot.status_code == 404


def test_admin_document_upload_rejects_unsafe_files_without_creating_documents(client):
    cid, _, _, _ = setup(client)
    sid = derived_snapshot(client, cid)

    assert (
        upload(client, cid, sid, "payload.exe", b"MZ", "application/octet-stream").status_code
        == 422
    )
    mimetype = upload(client, cid, sid, "manual.docx", b"PK\x03\x04bad", "text/plain")
    assert mimetype.status_code == 422
    assert error_code(mimetype) == "INVALID_MEDIA_TYPE"
    signature = upload(
        client,
        cid,
        sid,
        "manual.docx",
        b"not an office file",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert signature.status_code == 422
    assert error_code(signature) == "INVALID_SIGNATURE"
    traversal = upload(
        client,
        cid,
        sid,
        "../escape.docx",
        docx_manual(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert traversal.status_code == 422
    assert error_code(traversal) == "INVALID_DOCUMENT_TYPE"
    empty = upload(
        client,
        cid,
        sid,
        "empty.docx",
        b"",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert empty.status_code == 422
    assert error_code(empty) == "EMPTY_DOCUMENT"

    assert (
        upload(client, cid, sid, "manual.docx", docx_manual(), "application/pdf").status_code == 422
    )

    denied = upload(
        client,
        cid,
        sid,
        "manual.docx",
        docx_manual(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=READ,
    )
    assert denied.status_code == 403

    with session_factory().begin() as db:
        assert list(db.scalars(select(Document).where(Document.snapshot_id == sid))) == []


def test_admin_document_lifecycle_is_non_destructive_and_audited(client, monkeypatch, environment):
    cid, _, _, _ = setup(client)
    sid = derived_snapshot(client, cid)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    monkeypatch.setattr(environment, "document_max_bytes", 1024)
    too_large = upload(client, cid, sid, "huge.docx", docx_manual(), docx_mime)
    assert too_large.status_code == 413
    assert error_code(too_large) == "DOCUMENT_TOO_LARGE"
    monkeypatch.setattr(environment, "document_max_bytes", 20 * 1024**2)

    first = upload(client, cid, sid, "manual.docx", docx_manual(), docx_mime)
    assert first.status_code == 202, first.text
    run_job(first.json()["job_id"])
    first_id = first.json()["document_id"]

    duplicate = upload(client, cid, sid, "manual.docx", docx_manual("v2"), docx_mime)
    assert duplicate.status_code == 202, duplicate.text
    run_job(duplicate.json()["job_id"])
    second_id = duplicate.json()["document_id"]
    assert second_id != first_id

    with session_factory().begin() as db:
        first_doc = db.get(Document, first_id)
        second_doc = db.get(Document, second_id)
        assert first_doc.storage_key != second_doc.storage_key
        assert first_doc.sha256 != second_doc.sha256
        assert first_doc.filename == second_doc.filename == "manual.docx"
        old_sha = first_doc.sha256

    mismatch = client.post(
        f"/api/v1/snapshots/{sid}/documents/{first_id}/replace",
        headers=ADMIN,
        files={"file": ("manual.xlsx", xlsx_capabilities(), xlsx_mime)},
    )
    assert mismatch.status_code == 422
    assert error_code(mismatch) == "DOCUMENT_REPLACE_TYPE_MISMATCH"

    replaced = client.post(
        f"/api/v1/snapshots/{sid}/documents/{first_id}/replace",
        headers=ADMIN,
        files={"file": ("manual.docx", docx_manual("v2"), docx_mime)},
    )
    assert replaced.status_code == 202, replaced.text
    run_job(replaced.json()["job_id"])
    with session_factory().begin() as db:
        assert db.get(Document, first_id).sha256 != old_sha
        assert db.get(Document, first_id).parse_status == "parsed"

    reparsed = client.post(f"/api/v1/snapshots/{sid}/documents/{first_id}/reparse", headers=ADMIN)
    assert reparsed.status_code == 202, reparsed.text
    run_job(reparsed.json()["job_id"])
    with session_factory().begin() as db:
        first_segment_id = db.scalar(
            select(DocumentSegment.id)
            .where(DocumentSegment.document_id == first_id)
            .order_by(DocumentSegment.id)
            .limit(1)
        )

    broken = upload(
        client,
        cid,
        sid,
        "broken.pdf",
        b"%PDF-1.4\nthis is not a valid document",
        "application/pdf",
    )
    assert broken.status_code == 202, broken.text
    run_job(broken.json()["job_id"])
    with session_factory().begin() as db:
        broken_doc = db.get(Document, broken.json()["document_id"])
        assert broken_doc.parse_status == "failed"
        assert broken_doc.error_code

    with session_factory().begin() as db:
        db.add(
            Evidence(
                id=f"p31a-{uuid4()}",
                snapshot_id=sid,
                kind="document",
                source_ref={"segment_id": first_segment_id, "document_id": first_id},
                locator={"page": 1},
                source_hash="a" * 64,
                quote_hash="b" * 64,
                verification_state="source_verified",
            )
        )
    for response in [
        client.delete(f"/api/v1/snapshots/{sid}/documents/{first_id}", headers=ADMIN),
        client.post(f"/api/v1/snapshots/{sid}/documents/{first_id}/reparse", headers=ADMIN),
        client.post(
            f"/api/v1/snapshots/{sid}/documents/{first_id}/replace",
            headers=ADMIN,
            files={"file": ("manual.docx", docx_manual("v4"), docx_mime)},
        ),
    ]:
        assert response.status_code == 409
        assert error_code(response) == "DOCUMENT_IN_USE"

    with session_factory().begin() as db:
        db.get(Snapshot, sid).status = "published"
    for response in [
        client.delete(f"/api/v1/snapshots/{sid}/documents/{first_id}", headers=ADMIN),
        client.post(f"/api/v1/snapshots/{sid}/documents/{first_id}/reparse", headers=ADMIN),
    ]:
        assert response.status_code == 409
        assert error_code(response) == "DOCUMENT_STAGING_REQUIRED"

    audit = client.get(f"/api/v1/admin/audit?category_id={cid}&limit=200", headers=ADMIN)
    assert audit.status_code == 200
    actions = [item["action"] for item in audit.json()["items"]]
    assert "document.upload" in actions
    assert "documents.staging_snapshot.derived" in actions
    assert "documents.staging.replaced" in actions
    assert "documents.staging.reparse_requested" in actions


def test_admin_review_release_and_rollback_are_server_authorized_and_audited(client):
    cid, sid, claims, evidence = setup(client)
    revision = accept(client, claims[0], request(evidence["core/i_preview.py"]))

    queue = client.get(f"/api/v1/admin/snapshots/{sid}/review-queue", headers=ADMIN).json()
    assert queue["summary"]["accepted"] == 1
    assert queue["summary"]["unreviewed"] == 2
    assert queue["accepted_revision_ids"] == [revision["id"]]

    release = client.get(f"/api/v1/admin/snapshots/{sid}/release-check", headers=ADMIN).json()
    assert release["publishable"] is True
    assert release["accepted_revision_ids"] == [revision["id"]]
    assert release["blocking"] == []

    published = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "P3.1-A 管理员工作台发布",
        },
    )
    assert published.status_code == 200, published.text
    publication_id = published.json()["publication_id"]

    workspace = client.get(f"/api/v1/admin/categories/{cid}", headers=ADMIN).json()
    assert workspace["category"]["knowledge_ready"] is True
    assert workspace["category"]["active_publication_id"] == publication_id
    assert workspace["active_publication"]["capability_count"] == 1
    assert workspace["review"]["accepted"] == 1

    after = client.get(f"/api/v1/admin/snapshots/{sid}/release-check", headers=ADMIN).json()
    assert after["publishable"] is False
    assert after["items"][0]["published"] is True
    assert any("NO_UNPUBLISHED_ACCEPTED_REVISION" in item["errors"] for item in after["blocking"])

    rolled = client.post(
        f"/api/v1/categories/{cid}/rollback",
        headers=ADMIN,
        json={
            "publication_id": publication_id,
            "expected_active_publication_id": publication_id,
            "comment": "回滚到同一已发布清单，验证审计与固定引用",
        },
    )
    assert rolled.status_code == 200, rolled.text

    audit = client.get(f"/api/v1/admin/audit?category_id={cid}&limit=200", headers=ADMIN)
    actions = [item["action"] for item in audit.json()["items"]]
    assert "publication.publish" in actions
    assert "publication.rollback" in actions

    denied = client.post(
        f"/api/v1/categories/{cid}/rollback",
        headers=READ,
        json={"publication_id": publication_id, "expected_active_publication_id": publication_id},
    )
    assert denied.status_code == 403


def test_admin_cannot_override_model_authorization_from_request(client):
    cid, sid, _, _ = setup(client)
    with session_factory().begin() as db:
        from app.db.index_models import Module

        module_id = db.scalar(select(Module.id).where(Module.snapshot_id == sid).limit(1))
    response = client.post(
        f"/api/v1/snapshots/{sid}/investigate",
        headers=ADMIN,
        json={
            "module_ids": [module_id],
            "data_classification": "synthetic_public",
            "allow_model": True,
            "frontend_can_override": True,
        },
    )
    assert response.status_code == 422
    workspace = client.get(f"/api/v1/admin/categories/{cid}", headers=ADMIN).json()
    assert workspace["model_authorization"]["frontend_can_override"] is False
