"""Gate 2 核验逻辑：缺输入必须 BLOCKED，跨来源拼接必须 FAIL。"""

from scripts.p31b_gate2_verify import SYNTHETIC_SHA256, evaluate


def auth():
    return {
        "server_side_authorization_required": True,
        "frontend_can_override": False,
        "authorization_source": "database_record",
        "env_flags_are_approval": False,
        "builder": {
            "model_configured": True,
            "allow_synthetic": False,
            "allow_enterprise": True,
            "status": "AUTHORIZED",
            "reason_code": None,
            "active_authorization": {
                "id": "auth-1",
                "data_classification": "enterprise",
                "status": "active",
                "active": True,
                "expires_at": None,
                "approval_reference_present": True,
            },
        },
    }


def chain(root, ids):
    return {
        "valid": True,
        "error_code": None,
        "root_snapshot_id": root,
        "chain": [{"id": item} for item in ids],
    }


def category():
    return {
        "id": "c1",
        "code": "WAF",
        "snapshots": [
            {
                "id": "s1",
                "category_id": "c1",
                "status": "extracted",
                "index_status": "indexed",
                "source_sha256": "a" * 64,
                "derived_from_snapshot_id": None,
                "derivation_type": None,
                "data_classification": "enterprise",
            },
            {
                "id": "s2",
                "category_id": "c1",
                "status": "extracted",
                "index_status": "indexed",
                "source_sha256": "c" * 64,
                "derived_from_snapshot_id": "s1",
                "derivation_type": "document_update",
                "data_classification": "enterprise",
            },
        ],
        "provenance": [
            {
                "id": "p1",
                "snapshot_id": "s1",
                "document_id": None,
                "material_type": "source_code",
                "content_sha256": "a" * 64,
                "data_classification": "enterprise",
                "status": "confirmed",
            },
            {
                "id": "p2",
                "snapshot_id": "s2",
                "document_id": "d1",
                "material_type": "technical_document",
                "content_sha256": "b" * 64,
                "data_classification": "enterprise",
                "status": "confirmed",
            },
        ],
        "documents": [
            {
                "id": "d1",
                "snapshot_id": "s2",
                "sha256": "b" * 64,
                "parse_status": "parsed",
                "segment_count": 2,
                "data_classification": "enterprise",
            }
        ],
        "discoveries": [
            {
                "run_id": "r1",
                "snapshot_id": "s2",
                "status": "succeeded",
                "mode": "model",
                "error_code": None,
            }
        ],
        "publication": {
            "publication_id": "pub1",
            "snapshot_id": "s2",
            "capability_count": 2,
            "reviewed_capability_count": 2,
            "review_scope_valid": True,
        },
        "chains": {"s1": chain("s1", ["s1"]), "s2": chain("s1", ["s1", "s2"])},
    }


def observed(cat=None, auth_row=None):
    return {
        "categories": [cat or category()],
        "model_authorization": auth() if auth_row is None else auth_row,
    }


def test_complete_same_source_chain_passes():
    result = evaluate(observed())
    assert result["result"] == "PASS", result
    assert result["qualifying_categories"] == ["WAF"]
    assert result["counts"] == {
        "valid_sources": 1,
        "valid_documents": 1,
        "valid_discoveries": 1,
        "valid_publications": 1,
        "confirmed_synthetic": 0,
    }


def test_missing_provenance_is_blocked_not_passed():
    cat = category()
    cat["provenance"] = []
    result = evaluate(observed(cat))
    assert result["result"] == "BLOCKED"
    assert result["checks"]["source_provenance_confirmed"]["result"] == "BLOCKED"
    assert result["checks"]["document_provenance_confirmed"]["result"] == "BLOCKED"
    assert result["missing_inputs"]


def test_document_from_another_source_root_is_a_violation():
    cat = category()
    cat["snapshots"].append(
        {
            "id": "s3",
            "category_id": "c1",
            "status": "extracted",
            "index_status": "indexed",
            "source_sha256": "e" * 64,
            "derived_from_snapshot_id": "s9",
            "derivation_type": "document_update",
            "data_classification": "enterprise",
        }
    )
    cat["provenance"][1]["snapshot_id"] = "s3"
    cat["documents"][0]["snapshot_id"] = "s3"
    cat["chains"]["s3"] = chain("s9", ["s9", "s3"])
    result = evaluate(observed(cat))
    assert result["result"] == "FAIL"
    assert result["checks"]["same_category_and_derivation_chain"]["result"] == "FAIL"
    assert any("同一派生链" in item for item in result["violations"])


def test_document_on_same_root_sibling_branch_is_a_violation():
    cat = category()
    cat["snapshots"].append(
        {
            "id": "s3",
            "category_id": "c1",
            "status": "extracted",
            "index_status": "indexed",
            "source_sha256": "e" * 64,
            "derived_from_snapshot_id": "s1",
            "derivation_type": "document_update",
            "data_classification": "enterprise",
        }
    )
    cat["provenance"][1]["snapshot_id"] = "s3"
    cat["documents"][0]["snapshot_id"] = "s3"
    cat["chains"]["s3"] = chain("s1", ["s1", "s3"])
    result = evaluate(observed(cat))
    assert result["result"] == "FAIL"
    assert any("技术文档快照不在发布链" in item for item in result["violations"])


def test_discovery_on_same_root_sibling_branch_is_a_violation():
    cat = category()
    cat["snapshots"].append(
        {
            "id": "s3",
            "category_id": "c1",
            "status": "extracted",
            "index_status": "indexed",
            "source_sha256": "e" * 64,
            "derived_from_snapshot_id": "s1",
            "derivation_type": "document_update",
            "data_classification": "enterprise",
        }
    )
    cat["discoveries"][0]["snapshot_id"] = "s3"
    cat["chains"]["s3"] = chain("s1", ["s1", "s3"])
    result = evaluate(observed(cat))
    assert result["result"] == "FAIL"
    assert any("文档发现快照不在发布链" in item for item in result["violations"])


def test_publication_descendant_can_include_document_and_discovery_ancestors():
    cat = category()
    cat["snapshots"].append(
        {
            "id": "s3",
            "category_id": "c1",
            "status": "extracted",
            "index_status": "indexed",
            "source_sha256": "e" * 64,
            "derived_from_snapshot_id": "s2",
            "derivation_type": "capability_update",
            "data_classification": "enterprise",
        }
    )
    cat["publication"]["snapshot_id"] = "s3"
    cat["chains"]["s3"] = chain("s1", ["s1", "s2", "s3"])
    result = evaluate(observed(cat))
    assert result["result"] == "PASS", result


def test_confirmed_synthetic_hash_cannot_qualify():
    cat = category()
    synthetic = sorted(SYNTHETIC_SHA256)[0]
    cat["snapshots"][0]["source_sha256"] = synthetic
    cat["provenance"][0]["content_sha256"] = synthetic
    result = evaluate(observed(cat))
    assert result["result"] == "FAIL"
    assert result["checks"]["confirmed_materials_not_synthetic"]["result"] == "FAIL"
    assert result["counts"]["confirmed_synthetic"] == 1


def test_publication_without_reviewed_scope_is_blocked():
    cat = category()
    cat["publication"]["review_scope_valid"] = False
    result = evaluate(observed(cat))
    assert result["result"] == "BLOCKED"
    assert result["checks"]["reviewed_publication_scope_valid"]["result"] == "BLOCKED"
    assert "WAF" not in result["qualifying_categories"]


def test_publication_root_without_confirmed_source_is_blocked():
    cat = category()
    cat["provenance"][0]["snapshot_id"] = "s2"
    result = evaluate(observed(cat))
    assert result["result"] == "BLOCKED"
    assert any("根没有已确认真实源码" in item for item in result["missing_inputs"])


def test_missing_model_authorization_record_blocks_even_with_materials():
    result = evaluate(observed(auth_row={}))
    assert result["result"] == "BLOCKED"
    assert result["checks"]["enterprise_authorization_record"]["result"] == "BLOCKED"


def test_cross_category_source_is_not_accepted():
    cat = category()
    cat["snapshots"][0]["category_id"] = "c2"
    result = evaluate(observed(cat))
    assert result["result"] == "BLOCKED"
    assert result["counts"]["valid_sources"] == 0
