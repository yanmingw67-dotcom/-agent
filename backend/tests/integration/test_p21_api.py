"""P2.1 API 集成测试：任务、文件、修正审计、隔离和知识库隔离。"""

from uuid import uuid4

from sqlalchemy import func, select

from app.db.knowledge_models import CapabilityClaim
from app.db.models import Category
from app.db.session import session_factory
from app.db.tender_models import TenderCorrection, TenderExtractionJob, TenderRegion
from tests.conftest import ADMIN, READ
from tests.p21_assets import five_text, unnumbered_text, waf_docx


def category():
    with session_factory().begin() as db:
        row = Category(code="p21-" + uuid4().hex[:10], name="P2.1 测试品类")
        db.add(row)
        db.flush()
        return row.id


def create(client, text=None, key=None, headers=READ):
    payload = {"category_id": category(), "text": text or five_text()}
    if key:
        payload["idempotency_key"] = key
    return client.post("/api/v1/tender/extractions", headers=headers, json=payload)


def test_text_job_result_and_no_knowledge_claim(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        response = create(client)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        result = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ)
        assert result.status_code == 200
        data = result.json()
        assert data["status"] == "succeeded"
        assert data["extracted_count"] == 5
        assert data["coverage_incomplete"] is False
        assert len(data["parameters"]) == 5
        with session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(CapabilityClaim)) == 0


def test_unnumbered_job_marks_review_required(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        job_id = create(client, unnumbered_text()).json()["job_id"]
        data = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        assert data["extracted_count"] == 5
        assert data["review_required"] is True


def test_upload_docx_extracts_15_and_45(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        cid = category()
        response = client.post(
            "/api/v1/tender/extractions/upload",
            headers=READ,
            data={"category_id": cid},
            files={
                "file": (
                    "waf.docx",
                    waf_docx(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        data = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        assert data["extracted_count"] == 15
        assert sum(len(item["atomic_requirements"]) for item in data["parameters"]) == 45
        assert data["coverage_status"] == "complete"
        coverage = client.get(f"/api/v1/tender/extractions/{job_id}/coverage", headers=READ).json()[
            "records"
        ]
        assert any(item["disposition"] == "filtered" for item in coverage)


def test_user_isolation_and_idempotency(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        cid = category()
        payload = {"category_id": cid, "text": five_text(), "idempotency_key": "same-key"}
        first = client.post("/api/v1/tender/extractions", headers=READ, json=payload)
        second = client.post("/api/v1/tender/extractions", headers=READ, json=payload)
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert second.json()["created"] is False

        admin_job = create(client, headers=ADMIN).json()["job_id"]
        assert (
            client.get(f"/api/v1/tender/extractions/{admin_job}", headers=READ).status_code == 404
        )
        assert (
            client.get(f"/api/v1/tender/extractions/{admin_job}", headers=ADMIN).status_code == 200
        )

        read_job = create(client, headers=READ).json()["job_id"]
        assert (
            client.get(f"/api/v1/tender/extractions/{read_job}", headers=ADMIN).status_code == 404
        )


def test_edit_split_merge_add_and_audit(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        job_id = create(client).json()["job_id"]
        data = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        first = data["parameters"][0]
        edited = client.patch(
            f"/api/v1/tender/extractions/{job_id}/parameters/{first['id']}",
            headers=READ,
            json={"parameter_name": "人工改名"},
        )
        assert edited.status_code == 200
        assert edited.json()["parameter_name"] == "人工改名"
        confirmed = client.post(
            f"/api/v1/tender/extractions/{job_id}/parameters/{first['id']}/confirm",
            headers=READ,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

        split = client.post(
            f"/api/v1/tender/extractions/{job_id}/parameters/{first['id']}/split",
            headers=READ,
            json={
                "parts": [{"original_text": "（1）支持 A。"}, {"original_text": "（2）支持 B。"}]
            },
        )
        assert split.status_code == 200, split.text
        new_ids = [item["id"] for item in split.json()["parameters"]]
        merged = client.post(
            f"/api/v1/tender/extractions/{job_id}/merge",
            headers=READ,
            json={"parameter_ids": new_ids[:2], "parameter_name": "人工合并"},
        )
        assert merged.status_code == 200, merged.text
        added = client.post(
            f"/api/v1/tender/extractions/{job_id}/parameters",
            headers=READ,
            json={"original_text": "支持人工新增参数。"},
        )
        assert added.status_code == 201
        with session_factory()() as db:
            actions = set(
                db.scalars(select(TenderCorrection.action).where(TenderCorrection.job_id == job_id))
            )
            assert {"edit", "confirm", "split", "merge", "add"} <= actions
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(TenderRegion)
                    .where(TenderRegion.job_id == job_id)
                )
                > 0
            )


def test_invalid_upload_signature_rejected_without_job(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        cid = category()
        response = client.post(
            "/api/v1/tender/extractions/upload",
            headers=READ,
            data={"category_id": cid},
            files={"file": ("bad.docx", b"not a zip", "application/octet-stream")},
        )
        assert response.status_code == 422
        with session_factory()() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(TenderExtractionJob)
                    .where(TenderExtractionJob.category_id == cid)
                )
                == 0
            )
