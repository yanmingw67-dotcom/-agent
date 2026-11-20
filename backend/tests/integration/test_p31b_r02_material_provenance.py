"""R02：资料来源必须由管理员确认，并能核验品类与合法派生链。"""

import hashlib
from uuid import uuid4

from app.db.index_models import Document, DocumentSegment
from app.db.models import Category, Snapshot
from app.db.session import session_factory
from app.knowledge.provenance import SYNTHETIC_SHA256
from tests.conftest import ADMIN, READ


def sha(label):
    return hashlib.sha256(label.encode()).hexdigest()


def category_row(db):
    row = Category(code="p31b-r02-" + uuid4().hex[:10], name="R02 来源核验")
    db.add(row)
    db.flush()
    return row


def snapshot_row(
    db,
    category,
    *,
    digest=None,
    derived_from=None,
    derivation_type=None,
    index_status="indexed",
    status="extracted",
    data_classification="enterprise",
):
    row = Snapshot(
        category_id=category.id,
        source_sha256=digest or sha("snapshot-" + uuid4().hex),
        status=status,
        archive_key="r02/" + uuid4().hex,
        data_classification=data_classification,
        derived_from_snapshot_id=derived_from.id if derived_from else None,
        derivation_type=derivation_type,
        index_status=index_status,
    )
    db.add(row)
    db.flush()
    return row


def document_row(db, category, snapshot, *, digest=None, parse_status="parsed", segments=1):
    row = Document(
        category_id=category.id,
        snapshot_id=snapshot.id,
        filename="manual-" + uuid4().hex[:8] + ".docx",
        storage_key="r02/doc-" + uuid4().hex,
        sha256=digest or sha("document-" + uuid4().hex),
        size=1024,
        parse_status=parse_status,
        parser_version="test",
        parse_details={},
        origin_json={},
        data_classification="enterprise",
    )
    db.add(row)
    db.flush()
    for index in range(segments):
        db.add(
            DocumentSegment(
                snapshot_id=snapshot.id,
                document_id=row.id,
                locator={"paragraph": index + 1},
                text="已解析的技术文档正文",
                text_hash=sha(f"text-{row.id}-{index}"),
                sensitive=False,
            )
        )
    db.flush()
    return row


def confirm(client, category_id, body, headers=ADMIN):
    return client.post(
        f"/api/v1/admin/categories/{category_id}/material-provenance",
        headers=headers,
        json=body,
    )


def source_payload(snapshot):
    return {
        "snapshot_id": snapshot.id,
        "material_type": "source_code",
        "declared_origin": "厂商正式发布的 WAF 产品源码包",
        "usage_scope": "仅用于 P3.1-B 内部知识构建与验收，不对外分发",
        "confirmation_reference": "SOURCE-APPROVAL-" + uuid4().hex[:8],
    }


def document_payload(snapshot, document):
    return {
        "snapshot_id": snapshot.id,
        "material_type": "technical_document",
        "document_id": document.id,
        "declared_origin": "厂商正式技术手册与部署文档",
        "usage_scope": "仅用于 P3.1-B 内部知识构建与验收，不对外分发",
        "confirmation_reference": "DOC-APPROVAL-" + uuid4().hex[:8],
    }


def test_admin_confirms_source_and_document_then_revoke_reopens(client):
    with session_factory().begin() as db:
        category = category_row(db)
        source = snapshot_row(db, category)
        derived = snapshot_row(db, category, derived_from=source, derivation_type="document_update")
        document = document_row(db, category, derived)
        cid, sid, did = category.id, derived.id, document.id
        source_id = source.id

    created_source = confirm(client, cid, source_payload(source))
    assert created_source.status_code == 201, created_source.text
    assert created_source.json()["content_sha256"] == source.source_sha256
    created_doc = confirm(client, cid, document_payload(derived, document))
    assert created_doc.status_code == 201, created_doc.text
    assert created_doc.json()["document_id"] == did

    rows = client.get(f"/api/v1/admin/categories/{cid}/material-provenance", headers=ADMIN).json()
    assert len(rows["items"]) == 2
    assert all(row["status"] == "confirmed" for row in rows["items"])
    assert all(row["confirmation_reference"] for row in rows["items"])
    assert rows["items"][0]["declared_origin"]

    duplicate = confirm(client, cid, document_payload(derived, document))
    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "PROVENANCE_ALREADY_CONFIRMED"

    revoked = client.post(
        f"/api/v1/admin/material-provenance/{created_doc.json()['id']}/revoke", headers=ADMIN
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    again = confirm(client, cid, document_payload(derived, document))
    assert again.status_code == 201, again.text

    chain = client.get(f"/api/v1/admin/snapshots/{sid}/derivation-chain", headers=ADMIN)
    assert chain.status_code == 200
    assert chain.json()["valid"] is True
    assert chain.json()["root_snapshot_id"] == source_id
    assert [row["id"] for row in chain.json()["chain"]] == [source_id, sid]


def test_non_admin_cannot_confirm_provenance(client):
    with session_factory().begin() as db:
        category = category_row(db)
        source = snapshot_row(db, category)
        cid = category.id
    response = confirm(client, cid, source_payload(source), headers=READ)
    assert response.status_code == 403
    response = client.get(f"/api/v1/admin/categories/{cid}/material-provenance", headers=READ)
    assert response.status_code == 403


def test_synthetic_derived_or_unparsed_materials_are_rejected(client):
    with session_factory().begin() as db:
        category = category_row(db)
        synthetic = snapshot_row(db, category, digest=sorted(SYNTHETIC_SHA256)[0])
        root = snapshot_row(db, category)
        derived = snapshot_row(db, category, derived_from=root, derivation_type="document_update")
        unparsed = document_row(db, category, derived, parse_status="pending")
        empty = document_row(db, category, derived, segments=0)
        cid = category.id

    synthetic_response = confirm(client, cid, source_payload(synthetic))
    assert synthetic_response.status_code == 422
    assert synthetic_response.json()["error_code"] == "PROVENANCE_SYNTHETIC_REJECTED"

    derived_as_source = confirm(client, cid, source_payload(derived))
    assert derived_as_source.status_code == 422
    assert derived_as_source.json()["error_code"] == "PROVENANCE_SOURCE_NOT_ROOT"

    unparsed_response = confirm(client, cid, document_payload(derived, unparsed))
    assert unparsed_response.status_code == 422
    assert unparsed_response.json()["error_code"] == "PROVENANCE_DOCUMENT_NOT_PARSED"

    empty_response = confirm(client, cid, document_payload(derived, empty))
    assert empty_response.status_code == 422
    assert empty_response.json()["error_code"] == "PROVENANCE_DOCUMENT_EMPTY"


def test_snapshot_and_document_must_belong_to_the_same_category(client):
    with session_factory().begin() as db:
        first = category_row(db)
        second = category_row(db)
        foreign_source = snapshot_row(db, second)
        root = snapshot_row(db, first)
        derived = snapshot_row(db, first, derived_from=root, derivation_type="document_update")
        foreign_doc = document_row(db, second, snapshot_row(db, second))
        first_id = first.id
    foreign = confirm(client, first_id, source_payload(foreign_source))
    assert foreign.status_code == 404
    assert foreign.json()["error_code"] == "SNAPSHOT_NOT_FOUND"

    # 文档必须属于声明的快照，不能跨快照拼接。
    body = document_payload(derived, foreign_doc)
    cross_document = confirm(client, first_id, body)
    assert cross_document.status_code == 404
    assert cross_document.json()["error_code"] == "DOCUMENT_NOT_FOUND"


def test_broken_or_cross_category_derivation_chain_is_invalid(client):
    with session_factory().begin() as db:
        first = category_row(db)
        second = category_row(db)
        root = snapshot_row(db, first)
        broken = snapshot_row(db, first, derived_from=root, derivation_type="document_update")
        broken.derived_from_snapshot_id = "missing-" + uuid4().hex
        cross = snapshot_row(
            db, first, derived_from=snapshot_row(db, second), derivation_type="document_update"
        )
        broken_id, cross_id = broken.id, cross.id

    broken_chain = client.get(
        f"/api/v1/admin/snapshots/{broken_id}/derivation-chain", headers=ADMIN
    ).json()
    assert broken_chain["valid"] is False
    assert broken_chain["error_code"] == "DERIVATION_PARENT_MISSING"

    cross_chain = client.get(
        f"/api/v1/admin/snapshots/{cross_id}/derivation-chain", headers=ADMIN
    ).json()
    assert cross_chain["valid"] is False
    assert cross_chain["error_code"] == "DERIVATION_CATEGORY_MISMATCH"


def test_confirmation_reference_and_scope_are_required(client):
    with session_factory().begin() as db:
        category = category_row(db)
        source = snapshot_row(db, category)
        cid = category.id
    body = source_payload(source)
    body["confirmation_reference"] = ""
    assert confirm(client, cid, body).status_code == 422
    body = source_payload(source)
    body["usage_scope"] = "短"
    assert confirm(client, cid, body).status_code == 422
