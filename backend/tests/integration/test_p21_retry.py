"""R04：人工修正后的重试保护与主动重新提取。"""

from uuid import uuid4

from app.db.models import Category
from app.db.session import session_factory
from app.db.tender_models import TenderCorrection, TenderExtractionJob
from tests.conftest import READ
from tests.p21_assets import five_text


def category():
    with session_factory().begin() as db:
        row = Category(code="p21-retry-" + uuid4().hex[:10], name="重试保护测试品类")
        db.add(row)
        db.flush()
        return row.id


def _job(client, cid):
    response = client.post(
        "/api/v1/tender/extractions",
        headers=READ,
        json={"category_id": cid, "text": five_text()},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    detail = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ)
    assert detail.status_code == 200
    return job_id, detail.json()["parameters"][0]["id"]


def _retry(client, job_id):
    return client.post(f"/api/v1/tender/extractions/{job_id}/retry", headers=READ)


def test_retry_after_edit_does_not_overwrite_correction(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        job_id, parameter_id = _job(client, category())
        edited = client.patch(
            f"/api/v1/tender/extractions/{job_id}/parameters/{parameter_id}",
            headers=READ,
            json={"parameter_name": "人工确认后的名称", "original_text": "支持人工修正"},
        )
        assert edited.status_code == 200
        assert edited.json()["parameter_name"] == "人工确认后的名称"

        assert _retry(client, job_id).status_code == 409
        detail = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        parameter = next(item for item in detail["parameters"] if item["id"] == parameter_id)
        assert parameter["parameter_name"] == "人工确认后的名称"
        assert parameter["status"] == "corrected"

        with session_factory()() as db:
            assert db.query(TenderCorrection).filter_by(job_id=job_id).count() == 1


def test_retry_after_confirm_and_split_is_rejected(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        cid = category()
        confirmed_job, confirmed_parameter = _job(client, cid)
        confirmed = client.post(
            f"/api/v1/tender/extractions/{confirmed_job}/parameters/{confirmed_parameter}/confirm",
            headers=READ,
        )
        assert confirmed.status_code == 200
        assert _retry(client, confirmed_job).status_code == 409

        split_job, split_parameter = _job(client, cid)
        split = client.post(
            f"/api/v1/tender/extractions/{split_job}/parameters/{split_parameter}/split",
            headers=READ,
            json={"parts": [{"original_text": "拆分后的 A"}, {"original_text": "拆分后的 B"}]},
        )
        assert split.status_code == 200
        assert len(split.json()["parameters"]) == 2
        assert _retry(client, split_job).status_code == 409


def test_failed_job_can_retry_but_manual_changes_still_block(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        cid = category()
        job_id, parameter_id = _job(client, cid)
        client.patch(
            f"/api/v1/tender/extractions/{job_id}/parameters/{parameter_id}",
            headers=READ,
            json={"parameter_name": "人工结果"},
        )
        with session_factory().begin() as db:
            job = db.get(TenderExtractionJob, job_id)
            job.status = "failed"
            job.error_code = "TEST_FAILED"

        response = _retry(client, job_id)
        assert response.status_code == 409
        assert response.json()["error_code"] == "TENDER_JOB_HAS_MANUAL_CHANGES"
        with session_factory()() as db:
            assert db.get(TenderExtractionJob, job_id).status == "failed"


def test_reextract_creates_new_job_and_preserves_old_parameters(environment):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        job_id, parameter_id = _job(client, category())
        client.patch(
            f"/api/v1/tender/extractions/{job_id}/parameters/{parameter_id}",
            headers=READ,
            json={"parameter_name": "保留的人工名称"},
        )
        response = client.post(f"/api/v1/tender/extractions/{job_id}/reextract", headers=READ)
        assert response.status_code == 202
        body = response.json()
        assert body["created"] is True
        assert body["job_id"] != job_id
        assert body["source_job_id"] == job_id

        old = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        old_parameter = next(item for item in old["parameters"] if item["id"] == parameter_id)
        assert old_parameter["parameter_name"] == "保留的人工名称"
        assert old_parameter["status"] == "corrected"
        with session_factory()() as db:
            corrections = db.query(TenderCorrection).filter_by(job_id=job_id).all()
            assert corrections
            assert corrections[0].parameter_id == parameter_id
            new_job = db.get(TenderExtractionJob, body["job_id"])
            assert new_job.input_metadata["reextract_of"] == job_id
