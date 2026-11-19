"""P31-D06：提取任务状态、覆盖与有效参数数必须一致。

失败任务不得显示覆盖完整；无有效顶层参数不得显示成功；失败任务不能进入 P2.2。
"""

from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import Category
from app.db.session import session_factory
from app.db.tender_models import TenderExtractionJob
from app.tender import service
from app.tender.rules import extract
from app.tender.structure import parse_text
from tests.conftest import READ


@pytest.fixture(autouse=True)
def _environment(environment):
    yield environment


def category():
    with session_factory().begin() as db:
        row = Category(code="p31-d06-" + uuid4().hex[:10], name="P31 D06 测试品类")
        db.add(row)
        db.flush()
        return row.id


def text_job(text):
    with session_factory().begin() as db:
        job, created = service.create_text_job(
            db,
            principal_id="read-shared",
            category_id=category(),
            text=text,
        )
        assert created
        return job.id


def file_job(key):
    with session_factory().begin() as db:
        job, created = service.create_file_job(
            db,
            principal_id="read-shared",
            category_id=category(),
            key=key,
            file_hash="a" * 64,
            size=10,
            filename=key,
            suffix=Path(key).suffix,
        )
        assert created
        return job.id


def row(job_id):
    with session_factory()() as db:
        return db.get(TenderExtractionJob, job_id)


def view(job_id):
    with session_factory()() as db:
        return service.job_json(db, db.get(TenderExtractionJob, job_id))


def test_parse_exception_marks_failed_with_specific_reason():
    job_id = file_job("missing.docx")
    service.run_tender_job(job_id)
    job = row(job_id)
    assert job.status == "failed"
    assert job.coverage_status == "incomplete"
    assert job.error_code == "FILE_NOT_FOUND"
    assert job.coverage_json["failure_reason"]
    assert "文件" in job.coverage_json["failure_reason"]


def test_no_technical_parameter_is_failed_not_complete():
    job_id = text_job("技术参数要求\n本文件没有可量化的技术参数。")
    service.run_tender_job(job_id)
    job = row(job_id)
    assert job.extracted_count == 0
    assert job.status == "failed"
    assert job.coverage_status == "incomplete"
    assert job.error_code == "NO_TECHNICAL_PARAMETERS"
    assert job.coverage_json["failure_code"] == "NO_TECHNICAL_PARAMETERS"
    assert job.coverage_json["failure_reason"]


def test_all_regions_filtered_is_failed_not_complete():
    result = extract(parse_text("技术参数要求\n备注：无。\n友商产品支持 X。"))
    assert result.candidates == []
    assert result.coverage_status == "incomplete"
    assert result.details["failure_code"] == "NO_TECHNICAL_PARAMETERS"
    assert result.details["failure_reason"]
    job_id = text_job("技术参数要求\n备注：无。\n友商产品支持 X。")
    service.run_tender_job(job_id)
    job = row(job_id)
    assert (job.status, job.coverage_status) == ("failed", "incomplete")


def test_partial_valid_and_unparsable_is_partial_not_succeeded():
    text = "其他未尽事宜由采购人解释。\n1、支持 HTTP 解析。"
    result = extract(parse_text(text))
    assert len(result.candidates) == 1
    assert result.coverage_status == "incomplete"
    job_id = text_job(text)
    service.run_tender_job(job_id)
    job = row(job_id)
    assert job.status == "partial"
    assert job.coverage_status == "incomplete"
    assert job.extracted_count == 1
    assert job.error_code is None


def test_complete_success_has_consistent_counts_and_coverage():
    job_id = text_job("1、支持 HTTP 解析。\n2、支持 SQL 注入检测。")
    service.run_tender_job(job_id)
    job = row(job_id)
    assert job.status == "succeeded"
    assert job.coverage_status == "complete"
    assert job.extracted_count == 2
    assert job.error_code is None
    assert view(job_id)["coverage_incomplete"] is False


def test_unexpected_async_exception_fails_closed(monkeypatch):
    job_id = text_job("1、支持 HTTP 解析。")

    def boom(*args, **kwargs):
        raise RuntimeError("async boom")

    monkeypatch.setattr(service, "extract", boom)
    service.run_tender_job(job_id)
    job = row(job_id)
    assert job.status == "failed"
    assert job.coverage_status == "incomplete"
    assert job.error_code == "TENDER_INTERNAL_ERROR"
    assert job.coverage_json["failure_reason"]


def test_failed_job_retry_resets_coverage_and_can_succeed():
    job_id = text_job("技术参数要求\n备注：无。")
    service.run_tender_job(job_id)
    assert row(job_id).status == "failed"
    with session_factory().begin() as db:
        service.retry_job(db, job_id, "read-shared")
    with session_factory().begin() as db:
        job = db.get(TenderExtractionJob, job_id)
        assert job.status == "queued"
        assert job.error_code is None
        assert job.coverage_status == "incomplete"
        assert job.coverage_json["coverage_status"] == "incomplete"
        job.original_text = "1、支持 HTTP 解析。"
    service.run_tender_job(job_id)
    job = row(job_id)
    assert (job.status, job.coverage_status) == ("succeeded", "complete")
    assert job.extracted_count == 1


def test_frontend_shows_failed_state_and_blocks_empty_parameter_list():
    source = (
        Path(__file__).resolve().parents[2] / "frontend/src/pages/ExtractionDetail.tsx"
    ).read_text(encoding="utf-8")
    assert 'title="提取失败"' in source
    assert "job.error_message" in source
    assert "job.parameters.length > 0" in source
    assert "canEvaluate" in source


def test_failed_extraction_cannot_enter_p22(client):
    job_id = text_job("技术参数要求\n备注：无。")
    service.run_tender_job(job_id)
    assert row(job_id).status == "failed"
    response = client.post(
        "/api/v1/tender/evaluations",
        headers=READ,
        json={"extraction_job_id": job_id},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "EVALUATION_EXTRACTION_FAILED"


def test_historical_failed_job_with_complete_coverage_is_safe(client):
    job_id = text_job("技术参数要求\n备注：无。")
    service.run_tender_job(job_id)
    with session_factory().begin() as db:
        job = db.get(TenderExtractionJob, job_id)
        job.coverage_status = "complete"
        job.coverage_json = {"coverage_status": "complete"}
        job.error_code = "NO_TECHNICAL_PARAMETERS"
    response = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["coverage_status"] == "incomplete"
    assert data["coverage_incomplete"] is True
    assert "FAILED_JOB_COVERAGE_COMPLETE" in data["state_warnings"]
    assert data["coverage"]["coverage_status"] == "incomplete"
