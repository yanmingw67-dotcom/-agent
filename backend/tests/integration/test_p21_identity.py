"""R01：角色与用户身份分离，两个普通用户的任务/覆盖/修正接口互不可见。"""

import json
from uuid import uuid4

from app.core.config import get_settings
from app.db.models import Category
from app.db.session import session_factory
from app.db.tender_models import TenderExtractionJob
from tests.conftest import ADMIN, READ
from tests.p21_assets import five_text

ALICE = {"Authorization": "Bearer " + "x" * 48}
BOB = {"Authorization": "Bearer " + "y" * 48}


def category():
    with session_factory().begin() as db:
        row = Category(code="p21-id-" + uuid4().hex[:10], name="身份隔离测试品类")
        db.add(row)
        db.flush()
        return row.id


def test_two_read_users_are_isolated_for_all_job_and_correction_routes(environment, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("READ_USER_TOKENS", json.dumps({"alice": "x" * 48, "bob": "y" * 48}))
    get_settings.cache_clear()
    assert get_settings().read_token.get_secret_value() == "r" * 48

    with TestClient(create_app()) as client:
        cid = category()
        alice_job = client.post(
            "/api/v1/tender/extractions",
            headers=ALICE,
            json={"category_id": cid, "text": five_text()},
        )
        bob_job = client.post(
            "/api/v1/tender/extractions",
            headers=BOB,
            json={"category_id": cid, "text": five_text()},
        )
        assert alice_job.status_code == bob_job.status_code == 202
        alice_id = alice_job.json()["job_id"]
        bob_id = bob_job.json()["job_id"]
        assert alice_id != bob_id

        alice_result = client.get(f"/api/v1/tender/extractions/{alice_id}", headers=ALICE)
        assert alice_result.status_code == 200
        parameter_id = alice_result.json()["parameters"][0]["id"]

        # 同角色不同用户不能读取、下载、覆盖或修改。
        for headers in (BOB, READ, ADMIN):
            assert (
                client.get(f"/api/v1/tender/extractions/{alice_id}", headers=headers).status_code
                == 404
            )
            assert (
                client.get(
                    f"/api/v1/tender/extractions/{alice_id}/result", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/api/v1/tender/extractions/{alice_id}/coverage", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/retry", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/reextract", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.patch(
                    f"/api/v1/tender/extractions/{alice_id}/parameters/{parameter_id}",
                    headers=headers,
                    json={"parameter_name": "越权修改"},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/parameters/{parameter_id}/confirm",
                    headers=headers,
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/parameters/{parameter_id}/split",
                    headers=headers,
                    json={"parts": [{"original_text": "A"}, {"original_text": "B"}]},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/merge",
                    headers=headers,
                    json={"parameter_ids": [parameter_id, parameter_id]},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/extractions/{alice_id}/parameters",
                    headers=headers,
                    json={"original_text": "越权新增"},
                ).status_code
                == 404
            )

        assert client.get(f"/api/v1/tender/extractions/{alice_id}", headers=BOB).status_code == 404
        assert client.get(f"/api/v1/tender/extractions/{bob_id}", headers=ALICE).status_code == 404
        assert client.get("/api/v1/tender/extractions", headers=ALICE).json()["jobs"]
        assert client.get("/api/v1/tender/extractions", headers=BOB).json()["jobs"]

        with session_factory()() as db:
            owners = {
                db.get(TenderExtractionJob, alice_id).principal_id,
                db.get(TenderExtractionJob, bob_id).principal_id,
            }
        assert owners == {"alice", "bob"}
