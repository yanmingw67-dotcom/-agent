"""P31-D04：人工修改后必须重新解析全部量化约束并留下审计。"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import Category
from app.db.session import session_factory
from app.db.tender_models import TenderCorrection
from tests.conftest import READ


@pytest.fixture(autouse=True)
def _environment(environment):
    yield environment


def category():
    with session_factory().begin() as db:
        row = Category(code="p31-d04-" + uuid4().hex[:10], name="P31 D04 测试品类")
        db.add(row)
        db.flush()
        return row.id


def test_manual_edit_rebuilds_two_blank_quantified_constraints_and_audits():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/tender/extractions",
            headers=READ,
            json={"category_id": category(), "text": "1、资源占用不高于 10vCPU。"},
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        data = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        parameter = data["parameters"][0]

        edited = client.patch(
            f"/api/v1/tender/extractions/{job_id}/parameters/{parameter['id']}",
            headers=READ,
            json={"original_text": "资源占用不高于 10vCPU / ______GB 内存。"},
        )
        assert edited.status_code == 200, edited.text
        atoms = edited.json()["atomic_requirements"]
        blank_units = [item["unit"] for item in atoms if item["ambiguity"] == "blank_threshold"]
        assert blank_units == ["GB"]
        assert any(item["threshold"] == "10" and item["unit"] == "vCPU" for item in atoms)

        reloaded = client.get(f"/api/v1/tender/extractions/{job_id}", headers=READ).json()
        assert reloaded["parameters"][0]["status"] == "corrected"
        assert len(reloaded["parameters"][0]["atomic_requirements"]) == 2
        with session_factory()() as db:
            actions = set(
                db.scalars(select(TenderCorrection.action).where(TenderCorrection.job_id == job_id))
            )
            assert "edit" in actions
