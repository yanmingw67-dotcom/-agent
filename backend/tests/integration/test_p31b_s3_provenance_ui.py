"""S3 页面所用接口：只用隔离合成数据验证权限和刷新契约。"""

from app.db.session import session_factory
from tests.conftest import ADMIN, READ
from tests.integration.test_p31b_r02_material_provenance import (
    category_row,
    confirm,
    document_payload,
    document_row,
    snapshot_row,
    source_payload,
)


def test_revoke_permissions_and_refreshed_record(client):
    with session_factory().begin() as db:
        category = category_row(db)
        source = snapshot_row(db, category)
    result = confirm(client, category.id, source_payload(source))
    assert result.status_code == 201
    rid = result.json()["id"]
    url = f"/api/v1/admin/material-provenance/{rid}/revoke"
    assert client.post(url, headers=READ).status_code == 403
    records_url = f"/api/v1/admin/categories/{category.id}/material-provenance"
    assert client.get(records_url, headers=ADMIN).json()["items"][0]["status"] == "confirmed"
    assert client.post(url, headers=ADMIN).status_code == 200
    row = client.get(records_url, headers=ADMIN).json()["items"][0]
    assert row["id"] == rid and row["status"] == "revoked"
    assert row["revoked_at"] and row["revoked_by"] and row["confirmed_by"]
    assert client.post(url, headers=ADMIN).status_code == 409


def test_document_same_category_wrong_snapshot_rejected(client):
    with session_factory().begin() as db:
        category = category_row(db)
        first = snapshot_row(db, category)
        second = snapshot_row(db, category)
        document = document_row(db, category, first)
    response = confirm(client, category.id, document_payload(second, document))
    assert response.status_code == 404
    assert response.json()["error_code"] == "DOCUMENT_NOT_FOUND"
    response = client.get(
        f"/api/v1/admin/snapshots/{first.id}/derivation-chain", headers=READ
    )
    assert response.status_code == 403
