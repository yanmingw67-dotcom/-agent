"""P2.3 工作台、人工复核、导出与隔离的集成测试。

所有知识、招标样例均为测试构造；这些断言不证明真实产品能力。
"""

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import get_settings
from app.db.evaluation_models import EvaluationEvidence, ParameterEvaluation
from app.db.index_models import DocumentSegment
from app.db.knowledge_models import CapabilityClaim
from app.db.models import AuditEvent
from app.db.review_models import Publication
from app.db.session import session_factory
from app.db.workbench_models import EvaluationExportJob, EvaluationReview
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


_TOKEN_UNSET = object()


def download(client, export, headers=READ, token=_TOKEN_UNSET):
    request_headers = dict(headers or {})
    if token is _TOKEN_UNSET:
        token = export["download_token"]
    # 凭证只经专用请求头传递，互不影响 Authorization 身份校验。
    if token:
        request_headers["X-Export-Token"] = token
    return client.get(
        f"/api/v1/tender/workbench/exports/{export['export_id']}/download",
        headers=request_headers,
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
            "Access-Control-Request-Headers": "authorization, x-export-token",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    # 下载凭证经专用请求头传递，跨域预检必须放行，否则浏览器会把合法下载当成网络失败。
    assert "x-export-token" in allowed


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
        headers={"X-Export-Token": docx_export["download_token"]},
    )
    assert no_auth.status_code == 401
    query_only = client.get(
        f"/api/v1/tender/workbench/exports/{docx_export['export_id']}/download",
        headers=READ,
        params={"token": docx_export["download_token"]},
    )
    assert query_only.status_code == 403
    assert query_only.json()["error_code"] == "EXPORT_TOKEN_INVALID"
    assert "token=" not in docx_export["download_url"]
    assert docx_export["download_expires_at"]
    assert docx_export["download_expired"] is False
    assert docx_export["integrity_available"] is True
    assert docx_export["file_sha256"]


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


def _verdict_fingerprint(client, job_id):
    data = workbench(client, job_id)
    return [
        (
            item["parameter_id"],
            item["verdict"],
            tuple(
                (atom["requirement_id"], atom["verdict"]) for atom in item["atomic_requirements"]
            ),
        )
        for item in data["parameters"]
    ]


def test_r03_export_reverifies_source_and_restores_fail_closed(environment, client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "源文件再验证")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    before = _verdict_fingerprint(client, job_id)
    source = (
        Path(environment.source_root)
        / publication["snapshot_id"]
        / "source"
        / "core"
        / "i_preview.py"
    )
    assert source.is_file()
    original = source.read_bytes()
    source.chmod(0o640)
    source.parent.chmod(0o750)

    source.write_bytes(original + b"\n# export tamper\n")
    changed = create_export(client, job_id, "docx")
    assert changed["status"] == "failed", changed
    assert changed["error_code"] == "SOURCE_HASH_MISMATCH"
    assert _verdict_fingerprint(client, job_id) == before

    source.unlink()
    missing = create_export(client, job_id, "docx")
    assert missing["status"] == "failed", missing
    assert missing["error_code"] == "SOURCE_NOT_FOUND"
    assert _verdict_fingerprint(client, job_id) == before

    source.write_bytes(original)
    source.chmod(0o640)
    restored = create_export(client, job_id, "docx")
    assert restored["status"] == "succeeded", restored
    assert restored["file_sha256"] and len(restored["file_sha256"]) == 64
    body = download(client, restored)
    assert body.status_code == 200, body.text
    assert hashlib.sha256(body.content).hexdigest() == restored["file_sha256"]
    assert _verdict_fingerprint(client, job_id) == before


def test_r03_export_rejects_changed_document_segment(client):
    from tests.integration.test_p16_discovery import discover, document_review
    from tests.integration.test_p16_discovery import setup as document_setup

    cid, snapshot_id = document_setup(client)
    discover(client, snapshot_id)
    with session_factory()() as db:
        claim = next(
            item
            for item in db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == snapshot_id,
                    CapabilityClaim.name == "多租户策略隔离",
                )
            )
            if "sheet" in item.origin_json["locator"]
        )
    reviewed = client.post(
        f"/api/v1/capabilities/{claim.id}/review",
        headers=ADMIN,
        json=document_review(claim),
    )
    assert reviewed.status_code == 200, reviewed.text
    revision = reviewed.json()
    published = client.post(
        f"/api/v1/snapshots/{snapshot_id}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "P2.3 导出来源文档再验证用例",
        },
    )
    assert published.status_code == 200, published.text

    extraction_id, extraction = create_extraction(client, cid, text="1、支持多租户策略隔离。")
    assert extraction["extracted_count"] == 1
    job_id = create_evaluation(client, extraction_id)["job_id"]
    evidence = client.get(f"/api/v1/tender/evaluations/{job_id}/evidence", headers=READ)
    assert evidence.status_code == 200, evidence.text
    items = evidence.json()["evidence"]
    assert items and {item["kind"] for item in items} == {"document"}
    segment_id = items[0]["source_ref"]["segment_id"]
    with session_factory()() as db:
        segment = db.get(DocumentSegment, segment_id)
        original_text = segment.text
        original_hash = segment.text_hash
    before = _verdict_fingerprint(client, job_id)

    with session_factory().begin() as db:
        segment = db.get(DocumentSegment, segment_id)
        segment.text = original_text + "\n篡改后的文档分段"
    changed = create_export(client, job_id, "docx")
    assert changed["status"] == "failed", changed
    assert changed["error_code"] == "SEGMENT_HASH_MISMATCH"
    assert _verdict_fingerprint(client, job_id) == before

    with session_factory().begin() as db:
        segment = db.get(DocumentSegment, segment_id)
        segment.text = original_text
        segment.text_hash = original_hash
    restored = create_export(client, job_id, "docx")
    assert restored["status"] == "succeeded", restored
    assert download(client, restored).status_code == 200


def test_r03_download_detects_replaced_or_missing_export_file(environment, client):
    cid, _ = publish_synthetic(client, phrase_set(five=True), "导出文件完整性")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    export = create_export(client, job_id, "docx")
    assert export["status"] == "succeeded", export
    with session_factory()() as db:
        row = db.get(EvaluationExportJob, export["export_id"])
        path = Path(get_settings().export_store) / row.storage_key
    original = path.read_bytes()

    path.write_bytes(original + b"tampered")
    tampered = download(client, export)
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["error_code"] == "EXPORT_FILE_TAMPERED"

    path.write_bytes(original)
    assert download(client, export).status_code == 200

    path.unlink()
    missing = download(client, export)
    assert missing.status_code == 409
    assert missing.json()["error_code"] == "EXPORT_FILE_MISSING"
    path.write_bytes(original)


def test_r02_download_tokens_expire_refresh_and_never_leak_into_audit(client):
    cid, _ = publish_synthetic(client, phrase_set(five=True), "下载凭证")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    export = create_export(client, job_id, "docx")
    assert export["status"] == "succeeded", export
    export_id = export["export_id"]

    ok = download(client, export)
    assert ok.status_code == 200, ok.text
    wrong = download(client, export, token="q" * 43)
    assert wrong.status_code == 403
    assert wrong.json()["error_code"] == "EXPORT_TOKEN_INVALID"
    missing = download(client, export, token=None)
    assert missing.status_code == 403
    assert missing.json()["error_code"] == "EXPORT_TOKEN_INVALID"

    with session_factory().begin() as db:
        row = db.get(EvaluationExportJob, export_id)
        row.download_expires_at = datetime.now(UTC) - timedelta(seconds=5)
    expired = download(client, export)
    assert expired.status_code == 403, expired.text
    assert expired.json()["error_code"] == "EXPORT_TOKEN_EXPIRED"

    refreshed = client.post(
        f"/api/v1/tender/workbench/exports/{export_id}/download-token",
        headers=READ,
    )
    assert refreshed.status_code == 200, refreshed.text
    fresh_token = refreshed.json()["download_token"]
    assert fresh_token and fresh_token != export["download_token"]
    fresh_export = dict(export, download_token=fresh_token)
    assert download(client, fresh_export).status_code == 200
    stale = download(client, export)
    assert stale.status_code == 403
    assert stale.json()["error_code"] == "EXPORT_TOKEN_INVALID"

    other_token = client.post(
        f"/api/v1/tender/workbench/exports/{export_id}/download-token",
        headers=ADMIN,
    )
    assert other_token.status_code == 404

    with session_factory()() as db:
        events = list(
            db.scalars(select(AuditEvent).where(AuditEvent.action.like("evaluation.export.%")))
        )
    assert events
    serialized = "\n".join(json.dumps(row.details_json, ensure_ascii=False) for row in events)
    assert fresh_token not in serialized
    assert export["download_token"] not in serialized
    assert any(row.details_json.get("token_fingerprint") for row in events)


def test_r02_legacy_export_without_integrity_fails_closed(client):
    cid, _ = publish_synthetic(client, phrase_set(five=True), "旧导出")
    extraction_id, _ = create_extraction(client, cid)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    export = create_export(client, job_id, "xlsx")
    assert export["status"] == "succeeded", export
    with session_factory().begin() as db:
        row = db.get(EvaluationExportJob, export["export_id"])
        row.file_sha256 = None
        row.download_expires_at = None
        row.summary_json = {
            key: value for key, value in (row.summary_json or {}).items() if key != "file_sha256"
        }

    expired = download(client, export)
    assert expired.status_code == 403, expired.text
    assert expired.json()["error_code"] == "EXPORT_TOKEN_EXPIRED"

    with session_factory().begin() as db:
        row = db.get(EvaluationExportJob, export["export_id"])
        row.download_expires_at = datetime.now(UTC) + timedelta(hours=1)

    detail = client.get(
        f"/api/v1/tender/workbench/exports/{export['export_id']}",
        headers=READ,
    ).json()
    assert detail["integrity_available"] is False
    assert detail["file_sha256"] is None
    blocked = download(client, export)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error_code"] == "EXPORT_INTEGRITY_UNAVAILABLE"
    refresh = client.post(
        f"/api/v1/tender/workbench/exports/{export['export_id']}/download-token",
        headers=READ,
    )
    assert refresh.status_code == 409
    assert refresh.json()["error_code"] == "EXPORT_INTEGRITY_UNAVAILABLE"


def test_r01_manual_correction_round_trip_rebuilds_atoms_and_evaluation_count(client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "人工修正")
    extraction_id, extraction = create_extraction(client, cid)
    assert extraction["extracted_count"] == 5
    parameters = extraction["parameters"]
    edited = parameters[0]

    history = client.get(
        f"/api/v1/tender/extractions/{extraction_id}/corrections",
        headers=READ,
    )
    assert history.status_code == 200, history.text
    assert history.json()["items"] == []

    new_text = "支持 HTTP/1.1 与 HTTP/2，并提供连接复用"
    edited_response = client.patch(
        f"/api/v1/tender/extractions/{extraction_id}/parameters/{edited['id']}",
        headers=READ,
        json={"parameter_name": "人工修正后的协议能力", "original_text": new_text},
    )
    assert edited_response.status_code == 200, edited_response.text
    edited_body = edited_response.json()
    assert edited_body["parameter_name"] == "人工修正后的协议能力"
    assert edited_body["original_text"] == new_text
    assert edited_body["revision"] == 2
    assert edited_body["status"] == "corrected"
    assert edited_body["source_locator"]["anchor_status"] == "manual_edit"
    assert edited_body["source_locator"]["original_locator"]["kind"] == "text"
    assert edited_body["atomic_requirements"]
    for atom in edited_body["atomic_requirements"]:
        span = atom["source_span"]
        assert 0 <= span["start"] < span["end"] <= len(new_text)
        assert new_text[span["start"] : span["end"]].strip()
    leaf_ids = (
        {
            node["requirement_id"]
            for node in edited_body["logic_tree"].get("children", [])
            if node.get("kind") == "leaf"
        }
        if isinstance(edited_body.get("logic_tree"), dict)
        else set()
    )
    if leaf_ids:
        assert leaf_ids <= {atom["requirement_id"] for atom in edited_body["atomic_requirements"]}

    split_source = parameters[1]
    split_response = client.post(
        f"/api/v1/tender/extractions/{extraction_id}/parameters/{split_source['id']}/split",
        headers=READ,
        json={
            "parts": [
                {"parameter_name": "WebSocket 能力", "original_text": "支持 WebSocket 协议解析"},
                {"parameter_name": "SQL 注入能力", "original_text": "支持 SQL 注入检测与阻断"},
            ]
        },
    )
    assert split_response.status_code == 200, split_response.text
    split_parameters = split_response.json()["parameters"]
    assert len(split_parameters) == 2
    assert all(item["status"] == "corrected" for item in split_parameters)
    assert all(
        item["source_locator"]["anchor_status"] == "manual_split" for item in split_parameters
    )

    merge_ids = [parameters[2]["id"], parameters[3]["id"]]
    merge_response = client.post(
        f"/api/v1/tender/extractions/{extraction_id}/merge",
        headers=READ,
        json={"parameter_ids": merge_ids, "parameter_name": "合并后的检测能力"},
    )
    assert merge_response.status_code == 200, merge_response.text
    merged = merge_response.json()
    assert merged["parameter_name"] == "合并后的检测能力"
    assert merged["source_locator"]["anchor_status"] == "manual_merge"
    assert "多租户" in merged["original_text"]

    added_response = client.post(
        f"/api/v1/tender/extractions/{extraction_id}/parameters",
        headers=READ,
        json={"parameter_name": "日志导出能力", "original_text": "支持日志导出 PDF 报表"},
    )
    assert added_response.status_code == 201, added_response.text
    added = added_response.json()
    assert added["status"] == "corrected"
    assert added["atomic_requirements"]

    current = client.get(f"/api/v1/tender/extractions/{extraction_id}", headers=READ)
    assert current.status_code == 200, current.text
    current_body = current.json()
    assert len(current_body["parameters"]) == 6
    assert all(item["status"] != "superseded" for item in current_body["parameters"])
    assert [item["order_index"] for item in current_body["parameters"]] == [1, 2, 3, 4, 5, 6]
    assert {item["parameter_name"] for item in current_body["parameters"]} >= {
        "人工修正后的协议能力",
        "WebSocket 能力",
        "SQL 注入能力",
        "合并后的检测能力",
        "日志导出能力",
    }

    for parameter in current_body["parameters"]:
        if parameter["status"] == "confirmed":
            continue
        confirmed = client.post(
            f"/api/v1/tender/extractions/{extraction_id}/parameters/{parameter['id']}/confirm",
            headers=READ,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"

    corrections = client.get(
        f"/api/v1/tender/extractions/{extraction_id}/corrections",
        headers=READ,
    )
    assert corrections.status_code == 200, corrections.text
    rows = corrections.json()["items"]
    actions = [row["action"] for row in rows]
    assert {"edit", "split", "merge", "add", "confirm"} <= set(actions)
    edit_record = next(row for row in rows if row["action"] == "edit")
    assert edit_record["before"]["original_text"] == edited["original_text"]
    assert edit_record["after"]["original_text"] == new_text
    assert edit_record["actor"]

    evaluation = create_evaluation(client, extraction_id)
    results = workbench(client, evaluation["job_id"])
    assert results["job"]["status"] == "succeeded", results["job"]
    assert results["job"]["parameter_count"] == 6
    assert len(results["parameters"]) == 6
    assert results["job"]["publication_id"] == publication["publication_id"]
    by_name = {item["parameter_name"]: item for item in results["parameters"]}
    assert "人工修正后的协议能力" in by_name
    assert "日志导出能力" in by_name
    assert by_name["人工修正后的协议能力"]["original_text"] == new_text
    assert all(
        atom["evidence"] or atom["verdict"] in {"无法确认", "不支持"}
        for item in results["parameters"]
        for atom in item["atomic_requirements"]
    )


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
                headers={**headers, "X-Export-Token": export["download_token"]},
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
