import json

from sqlalchemy import select

from app.db.index_models import Document
from app.db.session import session_factory
from app.ingestion.doc_parsers import parse_document
from tests.conftest import ADMIN
from tests.integration.test_p12_index_retrieval import indexed, run_job, tool
from tests.p12_remediation_assets import many_merges_xlsx, mixed_pdf


def test_r03_mixed_pdf_keeps_both_text_pages_and_middle_page_status(client):
    cid, sid = indexed(client)
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": ("mixed.pdf", mixed_pdf(), "application/pdf")},
    )
    assert response.status_code == 202, response.text
    ids = response.json()
    run_job(ids["job_id"])
    with session_factory()() as db:
        doc = db.get(Document, ids["document_id"])
        assert doc.parse_status == "partial"
        assert doc.parse_details["pages"] == [
            {"page": 1, "status": "parsed"},
            {"page": 2, "status": "empty_page"},
            {"page": 3, "status": "parsed"},
        ]
    for text, page in [("first searchable", 1), ("last searchable", 3)]:
        found = tool(client, cid, sid, "search_documents", query=text).json()
        assert len(found["results"]) == 1 and "partial_document" in " ".join(found["warnings"])
        item = found["results"][0]
        assert item["locator"]["page"] == page and item["status"] == "partial"
        reread = tool(client, cid, sid, "read_document", segment_id=item["id"]).json()["results"][0]
        assert reread["sha256"] == item["sha256"] and reread["snippet"] == item["snippet"]
    missing = tool(client, cid, sid, "search_documents", query="no_such_text").json()
    assert not missing["results"] and missing["coverage_incomplete"]
    listed = client.get(f"/api/v1/snapshots/{sid}/documents", headers=ADMIN).json()
    assert listed[0]["parse_details"]["pages"][1]["page"] == 2


def test_r05_sheet_metadata_not_copied_into_every_cell(tmp_path, environment):
    path = tmp_path / "large.xlsx"
    path.write_bytes(many_merges_xlsx())
    details = {}
    segments = list(parse_document(path, ".xlsx", environment, details))
    all_ranges = details["sheet_merged_ranges"]["Scale"]
    assert len(all_ranges) == 200 and len(segments) == 3800
    assert all(len(locator["merged_ranges"]) <= 1 for locator, _ in segments)
    anchor = next(loc for loc, _ in segments if loc["cell"] == "A100")
    other = next(loc for loc, _ in segments if loc["cell"] == "D100")
    assert anchor["merged_ranges"] == ["A100:B100"] and other["merged_ranges"] == []
    new_bytes = len(json.dumps(details)) + sum(len(json.dumps(loc)) for loc, _ in segments)
    old_bytes = sum(len(json.dumps({**loc, "merged_ranges": all_ranges})) for loc, _ in segments)
    assert new_bytes < old_bytes / 5
    print(
        json.dumps(
            {
                "cells": len(segments),
                "merged_ranges": 200,
                "old_locator_bytes": old_bytes,
                "new_metadata_locator_bytes": new_bytes,
            }
        )
    )


def test_r05_merge_details_persisted_once_on_document(client):
    cid, sid = indexed(client)
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={
            "file": (
                "merged.xlsx",
                many_merges_xlsx(30, 10),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 202, response.text
    ids = response.json()
    run_job(ids["job_id"])
    with session_factory()() as db:
        doc = db.scalar(select(Document).where(Document.id == ids["document_id"]))
        assert len(doc.parse_details["sheet_merged_ranges"]["Scale"]) == 10
