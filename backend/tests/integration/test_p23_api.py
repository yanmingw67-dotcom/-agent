"""P2.3 工作台、人工复核、导出与隔离的集成测试。

所有知识、招标样例均为测试构造；这些断言不证明真实产品能力。
"""

import io
import json

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import get_settings
from app.db.evaluation_models import EvaluationEvidence, ParameterEvaluation
from app.db.review_models import Publication
from app.db.session import session_factory
from app.db.workbench_models import EvaluationReview
from app.main import create_app
from tests.conftest import ADMIN, READ
from tests.integration.test_p22_api import (
    create_evaluation,
    create_extraction,
    phrase_set,
    publish_synthetic,
)
from tests.p21_assets import PARAM_NAMES, waf_docx


def workbench(client, job_id, headers=READ):
    response = client.get(f"/api/v1/tender/workbench/tasks/{job_id}/results", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def create_export(client, job_id, fmt, headers=READ):
    response = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/exports",
        headers=headers,
        json={"format": fmt},
    )
    assert response.status_code == 202, response.text
    export_id = response.json()["export_id"]
    detail = client.get(f"/api/v1/tender/workbench/exports/{export_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()


def download(client, export, headers=READ, token=None):
    return client.get(
        f"/api/v1/tender/workbench/exports/{export['export_id']}/download",
        headers=headers,
        params={"token": token or export["download_token"]},
    )


def supported_parameter(results):
    for item in results["parameters"]:
        if item["verdict"] == "支持" and not item["error_code"]:
            return item
    raise AssertionError("测试知识应至少产生一个支持参数")


def test_workbench_cors_preflight_allows_configured_frontend(client):
    response = client.options(
        "/api/v1/tender/workbench/tasks",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_task_list_review_history_and_automatic_result_is_immutable(client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "工作台")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]

    listing = client.get("/api/v1/tender/workbench/tasks", headers=READ)
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["job_id"] == job_id
    assert items[0]["counts"]["支持"] >= 1
    assert items[0]["reviewed_count"] == 0
    assert items[0]["pending_review_count"] == 5
    assert items[0]["exportable"] is True

    filtered = client.get(
        "/api/v1/tender/workbench/tasks",
        headers=READ,
        params={"status": "succeeded", "category_id": cid},
    ).json()
    assert [item["job_id"] for item in filtered["items"]] == [job_id]
    assert (
        client.get(
            "/api/v1/tender/workbench/tasks",
            headers=READ,
            params={"status": "failed"},
        ).json()["items"]
        == []
    )

    results = workbench(client, job_id)
    assert results["job"]["publication_id"] == publication["publication_id"]
    assert results["job"]["parameter_count"] == 5
    assert results["job"]["reviewed_count"] == 0
    assert results["job"]["evidence_context_available"] is True
    assert len(results["parameters"]) == 5
    parameter = supported_parameter(results)
    assert parameter["review_status"] == "pending"
    assert parameter["atomic_requirements"]
    assert parameter["evidence"]
    assert parameter["evidence"][0]["source_type_label"] in {"源码", "技术文档"}
    assert parameter["evidence"][0]["supported_scope_text"]
    assert parameter["evidence"][0]["limitations_text"]
    assert parameter["logic_reason"]

    with session_factory()() as db:
        before = db.scalar(
            select(ParameterEvaluation).where(
                ParameterEvaluation.job_id == job_id,
                ParameterEvaluation.parameter_id == parameter["parameter_id"],
            )
        )
        before_verdict = before.verdict
        before_evidence = sorted(
            item.evidence_id
            for item in db.scalars(
                select(EvaluationEvidence).where(
                    EvaluationEvidence.parameter_evaluation_id == before.id
                )
            )
        )

    accepted = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/parameters/{parameter['parameter_id']}/review",
        headers=READ,
        json={"action": "accept", "comment": "合成测试：认可自动结论"},
    )
    assert accepted.status_code == 201, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["auto_verdict"] == parameter["verdict"]
    assert accepted_body["review"]["final_verdict"] == parameter["verdict"]
    assert accepted_body["review"]["recognized"] is True

    final = "不支持" if parameter["verdict"] == "支持" else "支持"
    adjusted = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/parameters/{parameter['parameter_id']}/review",
        headers=READ,
        json={
            "action": "adjust",
            "final_verdict": final,
            "adjust_reason": "合成测试：人工应答与自动结论不同",
            "comment": "保留自动结论，供后续复核",
        },
    )
    assert adjusted.status_code == 201, adjusted.text
    adjusted_body = adjusted.json()
    assert adjusted_body["auto_verdict"] == parameter["verdict"]
    assert adjusted_body["review"]["final_verdict"] == final
    assert adjusted_body["review"]["auto_verdict"] == parameter["verdict"]
    assert adjusted_body["review"]["review_status"] == "adjusted"
    assert adjusted_body["review_summary"]["reviewed"] == 1
    assert adjusted_body["review_summary"]["adjusted"] == 1

    same = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/parameters/{parameter['parameter_id']}/review",
        headers=READ,
        json={
            "action": "adjust",
            "final_verdict": parameter["verdict"],
            "adjust_reason": "与自动结论相同",
        },
    )
    assert same.status_code == 422
    assert same.json()["error_code"] == "REVIEW_VERDICT_UNCHANGED"

    history = client.get(
        f"/api/v1/tender/workbench/tasks/{job_id}/parameters/{parameter['parameter_id']}/reviews",
        headers=READ,
    )
    assert history.status_code == 200
    rows = history.json()["items"]
    assert len(rows) == 2
    assert {row["action"] for row in rows} == {"accept", "adjust"}
    assert all(row["auto_verdict"] == parameter["verdict"] for row in rows)

    after = workbench(client, job_id)
    after_parameter = next(
        item for item in after["parameters"] if item["parameter_id"] == parameter["parameter_id"]
    )
    assert after_parameter["verdict"] == parameter["verdict"]
    assert after_parameter["human_final_verdict"] == final
    assert after_parameter["review_status"] == "adjusted"

    with session_factory()() as db:
        row = db.scalar(
            select(ParameterEvaluation).where(
                ParameterEvaluation.job_id == job_id,
                ParameterEvaluation.parameter_id == parameter["parameter_id"],
            )
        )
        assert row.verdict == before_verdict
        after_evidence = sorted(
            item.evidence_id
            for item in db.scalars(
                select(EvaluationEvidence).where(
                    EvaluationEvidence.parameter_evaluation_id == row.id
                )
            )
        )
        assert after_evidence == before_evidence
        review_rows = list(
            db.scalars(select(EvaluationReview).where(EvaluationReview.job_id == job_id))
        )
        assert len(review_rows) == 2


def test_exports_are_editable_consistent_and_formula_safe(client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "导出")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    results = workbench(client, job_id)
    parameter = supported_parameter(results)
    final = "不支持" if parameter["verdict"] == "支持" else "支持"
    reviewed = client.post(
        f"/api/v1/tender/workbench/tasks/{job_id}/parameters/{parameter['parameter_id']}/review",
        headers=READ,
        json={
            "action": "adjust",
            "final_verdict": final,
            "adjust_reason": "合成测试：人工调整理由",
            "comment": "合成测试：人工复核意见",
        },
    )
    assert reviewed.status_code == 201, reviewed.text

    docx_export = create_export(client, job_id, "docx")
    assert docx_export["status"] == "succeeded", docx_export
    assert docx_export["summary"]["publication_id"] == publication["publication_id"]
    assert docx_export["parameter_count"] == 5
    assert docx_export["atomic_count"] == sum(
        len(item["atomic_requirements"]) for item in results["parameters"]
    )
    doc_response = download(client, docx_export)
    assert doc_response.status_code == 200, doc_response.text
    assert doc_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert doc_response.headers["cache-control"] == "no-store"
    document = Document(io.BytesIO(doc_response.content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            text += "\n" + "\t".join(cell.text for cell in row.cells)
    assert "产品技术参数符合性评估报告" in text
    assert "系统自动评估结论与人工复核意见分列呈现" in text
    assert "人工复核与人工应答（不改变系统自动结论）" in text
    assert parameter["parameter_name"] in text
    assert "人工调整理由" in text
    assert "合成测试：人工调整理由" in text
    assert "提供检测报告及功能截图" not in text
    assert len(document.tables) >= 4

    xlsx_export = create_export(client, job_id, "xlsx")
    assert xlsx_export["status"] == "succeeded", xlsx_export
    xlsx_response = download(client, xlsx_export)
    assert xlsx_response.status_code == 200
    workbook = load_workbook(io.BytesIO(xlsx_response.content))
    assert workbook.sheetnames == [
        "评估结果汇总",
        "原子子要求明细",
        "待确认与问题清单",
        "证据索引",
    ]
    summary = workbook["评估结果汇总"]
    assert [cell.value for cell in summary[1]][:4] == [
        "序号",
        "参数名称",
        "招标参数原文",
        "系统自动结论",
    ]
    assert summary.max_row == 6
    assert workbook["原子子要求明细"].max_row == 1 + docx_export["atomic_count"]
    summary_text = "\n".join(str(cell.value or "") for row in summary.iter_rows() for cell in row)
    assert parameter["parameter_name"] in summary_text
    assert "提供检测报告及功能截图" not in summary_text
    assert all(
        cell.data_type != "f"
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )

    wrong = download(client, docx_export, token="x" * 43)
    assert wrong.status_code == 403
    assert wrong.json()["error_code"] == "EXPORT_TOKEN_INVALID"
    no_auth = client.get(
        f"/api/v1/tender/workbench/exports/{docx_export['export_id']}/download",
        params={"token": docx_export["download_token"]},
    )
    assert no_auth.status_code == 401


def test_fifteen_parameters_and_45_atoms_export(client):
    cid, _ = publish_synthetic(client, phrase_set(PARAM_NAMES), "十五项导出")
    response = client.post(
        "/api/v1/tender/extractions/upload",
        headers=READ,
        data={"category_id": cid},
        files={
            "file": (
                "synthetic-waf.docx",
                waf_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 202, response.text
    extraction_id = response.json()["job_id"]
    extraction = client.get(f"/api/v1/tender/extractions/{extraction_id}", headers=READ).json()
    assert extraction["extracted_count"] == 15
    assert sum(len(item["atomic_requirements"]) for item in extraction["parameters"]) == 45
    job_id = create_evaluation(client, extraction_id)["job_id"]
    results = workbench(client, job_id)
    assert len(results["parameters"]) == 15
    assert sum(len(item["atomic_requirements"]) for item in results["parameters"]) == 45

    docx_export = create_export(client, job_id, "docx")
    assert docx_export["status"] == "succeeded", docx_export
    document = Document(io.BytesIO(download(client, docx_export).content))
    main_table = next(table for table in document.tables if table.rows[0].cells[0].text == "序号")
    assert len(main_table.rows) == 16
    for name in PARAM_NAMES:
        assert any(name in cell.text for row in main_table.rows for cell in row.cells)

    xlsx_export = create_export(client, job_id, "xlsx")
    assert xlsx_export["status"] == "succeeded", xlsx_export
    workbook = load_workbook(io.BytesIO(download(client, xlsx_export).content))
    assert workbook["评估结果汇总"].max_row == 16
    assert workbook["原子子要求明细"].max_row == 46


def test_other_user_and_admin_cannot_see_or_export(client):
    cid, _ = publish_synthetic(client, phrase_set(five=True), "越权")
    extraction_id, _ = create_extraction(client, cid, headers=READ)
    job_id = create_evaluation(client, extraction_id, headers=READ)["job_id"]
    export = create_export(client, job_id, "docx", headers=READ)

    for headers in (ADMIN,):
        assert (
            client.get(f"/api/v1/tender/workbench/tasks/{job_id}", headers=headers).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/tender/workbench/tasks/{job_id}/results", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/tender/workbench/tasks/{job_id}/exports", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/tender/workbench/exports/{export['export_id']}",
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/tender/workbench/exports/{export['export_id']}/download",
                headers=headers,
                params={"token": export["download_token"]},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/tender/workbench/tasks/{job_id}/parameters/"
                f"{workbench(client, job_id)['parameters'][0]['parameter_id']}/review",
                headers=headers,
                json={"action": "accept"},
            ).status_code
            == 404
        )


def test_tampered_publication_marks_page_unavailable_and_export_fails_closed(client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "发布篡改")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    with session_factory().begin() as db:
        row = db.get(Publication, publication["publication_id"])
        original = dict(row.payload_json)
        row.payload_json = {**row.payload_json, "tampered": True}

    page = workbench(client, job_id)
    assert page["job"]["evidence_context_available"] is False
    assert page["job"]["publication_context_error"] == "PUBLICATION_TAMPERED"
    assert all(
        evidence["context_status"] == "unavailable"
        for parameter in page["parameters"]
        for evidence in parameter["evidence"]
    )

    export = create_export(client, job_id, "docx")
    assert export["status"] == "failed"
    assert export["error_code"] == "EXPORT_EVIDENCE_CONTEXT_UNAVAILABLE"
    assert export["download_url"]
    blocked = download(client, export)
    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "EXPORT_NOT_READY"

    with session_factory().begin() as db:
        db.get(Publication, publication["publication_id"]).payload_json = original
    restored = create_export(client, job_id, "docx")
    assert restored["status"] == "succeeded", restored


def test_cross_user_tokens_are_isolated(environment, monkeypatch):
    monkeypatch.setenv("READ_USER_TOKENS", json.dumps({"alice": "x" * 48, "bob": "y" * 48}))
    get_settings.cache_clear()
    alice = {"Authorization": "Bearer " + "x" * 48}
    bob = {"Authorization": "Bearer " + "y" * 48}
    with TestClient(create_app()) as client:
        cid, _ = publish_synthetic(client, phrase_set(five=True), "双用户")
        extraction_id, _ = create_extraction(client, cid, headers=alice)
        job_id = create_evaluation(client, extraction_id, headers=alice)["job_id"]
        assert client.get("/api/v1/tender/workbench/tasks", headers=alice).json()["items"]
        assert client.get("/api/v1/tender/workbench/tasks", headers=bob).json()["items"] == []
        assert (
            client.get(f"/api/v1/tender/workbench/tasks/{job_id}", headers=bob).status_code == 404
        )
        export = create_export(client, job_id, "docx", headers=alice)
        assert (
            client.get(
                f"/api/v1/tender/workbench/exports/{export['export_id']}",
                headers=bob,
            ).status_code
            == 404
        )
