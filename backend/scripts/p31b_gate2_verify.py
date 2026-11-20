"""P3.1-B Gate 2 的纯核验逻辑。

输入只包含脱敏后的管理员 API 观测结果；核验重点是：
- 管理员已确认来源与适用范围；
- 源码、文档、文档发现与发布物处于同一品类和合法派生链；
- 已审核能力确实来自该发布物快照；
- 任一条件缺失即 BLOCKED，跨来源拼接则 FAIL。
"""

SYNTHETIC_SHA256 = {
    "d082d172f6a6e7d39d36809625a94ef278a6de7d65df718bc247d0c1bc55c65e",
    "fda28d7f561e47ff1aa9d7584d0b6322f520bc87cab06257901c6dd06f076d39",
}


def _map(items, key="id"):
    return {item[key]: item for item in items or [] if item.get(key)}


def _chain(category, snapshot_id):
    return (category.get("chains") or {}).get(snapshot_id) or {
        "valid": False,
        "error_code": "DERIVATION_CHAIN_MISSING",
        "chain": [],
        "root_snapshot_id": None,
    }


def _chain_ids(result):
    return [item.get("id") for item in result.get("chain") or []]


def _valid_source(confirmation, category):
    snapshots = _map(category.get("snapshots"))
    snapshot = snapshots.get(confirmation.get("snapshot_id"))
    return bool(
        confirmation.get("status") == "confirmed"
        and confirmation.get("material_type") == "source_code"
        and confirmation.get("data_classification") == "enterprise"
        and confirmation.get("content_sha256") not in SYNTHETIC_SHA256
        and snapshot
        and snapshot.get("category_id") == category.get("id")
        and snapshot.get("derivation_type") is None
        and snapshot.get("derived_from_snapshot_id") is None
        and snapshot.get("index_status") == "indexed"
        and snapshot.get("data_classification") == "enterprise"
        and snapshot.get("source_sha256") == confirmation.get("content_sha256")
    )


def _valid_document(confirmation, category):
    snapshots = _map(category.get("snapshots"))
    documents = _map(category.get("documents"))
    snapshot = snapshots.get(confirmation.get("snapshot_id"))
    document = documents.get(confirmation.get("document_id"))
    return bool(
        confirmation.get("status") == "confirmed"
        and confirmation.get("material_type") == "technical_document"
        and confirmation.get("data_classification") == "enterprise"
        and confirmation.get("content_sha256") not in SYNTHETIC_SHA256
        and snapshot
        and snapshot.get("category_id") == category.get("id")
        and snapshot.get("index_status") == "indexed"
        and snapshot.get("data_classification") == "enterprise"
        and document
        and document.get("snapshot_id") == snapshot.get("id")
        and document.get("parse_status") == "parsed"
        and document.get("segment_count", 0) > 0
        and document.get("data_classification") == "enterprise"
        and document.get("sha256") == confirmation.get("content_sha256")
    )


def _valid_discovery(run, category):
    snapshots = _map(category.get("snapshots"))
    snapshot = snapshots.get(run.get("snapshot_id"))
    return bool(
        run.get("status") == "succeeded"
        and run.get("mode") in {"model", "hybrid"}
        and run.get("snapshot_id")
        and snapshot
        and snapshot.get("category_id") == category.get("id")
        and snapshot.get("data_classification") == "enterprise"
        and _chain(category, snapshot["id"]).get("valid")
    )


def _valid_publication(publication, category):
    snapshots = _map(category.get("snapshots"))
    snapshot = snapshots.get(publication.get("snapshot_id"))
    return bool(
        publication.get("publication_id")
        and publication.get("capability_count", 0) > 0
        and publication.get("reviewed_capability_count", 0) > 0
        and publication.get("review_scope_valid") is True
        and snapshot
        and snapshot.get("category_id") == category.get("id")
        and _chain(category, snapshot["id"]).get("valid")
    )


def evaluate(observed):
    cats = observed.get("categories") or []
    auth = observed.get("model_authorization") or {}
    builder = auth.get("builder") or {}
    active = builder.get("active_authorization") or {}
    checks = {}
    missing = []
    violations = []
    qualifying = []

    def record(name, status, detail):
        checks[name] = {"result": status, "detail": detail}

    configured = bool(builder.get("model_configured"))
    record("model_configured", "PASS" if configured else "BLOCKED", f"configured={configured}")
    authorized = bool(
        configured
        and builder.get("status") == "AUTHORIZED"
        and active.get("id")
        and active.get("data_classification") == "enterprise"
    )
    record(
        "enterprise_authorization_record",
        "PASS" if authorized else "BLOCKED",
        f"status={builder.get('status')} has_record={bool(active.get('id'))}",
    )
    env_provenance = (
        auth.get("env_flags_are_approval") is False
        and auth.get("authorization_source") == "database_record"
    )
    record(
        "env_flags_are_not_approval",
        "PASS" if env_provenance else ("BLOCKED" if not auth else "FAIL"),
        f"source={auth.get('authorization_source')}",
    )
    record(
        "synthetic_egress_disabled",
        (
            "PASS"
            if builder.get("allow_synthetic") is False
            else "FAIL"
            if builder.get("allow_synthetic") is True
            else "BLOCKED"
        ),
        f"allow_synthetic={builder.get('allow_synthetic')}",
    )
    record(
        "frontend_cannot_override_authorization",
        (
            "PASS"
            if auth.get("frontend_can_override") is False
            else "FAIL"
            if "frontend_can_override" in auth
            else "BLOCKED"
        ),
        f"frontend_can_override={auth.get('frontend_can_override')}",
    )

    valid_sources = 0
    valid_documents = 0
    valid_discoveries = 0
    valid_publications = 0
    synthetic_confirmed = 0
    for category in cats:
        provenance = category.get("provenance") or []
        source_rows = [row for row in provenance if row.get("material_type") == "source_code"]
        document_rows = [
            row for row in provenance if row.get("material_type") == "technical_document"
        ]
        sources = [row for row in source_rows if _valid_source(row, category)]
        documents = [row for row in document_rows if _valid_document(row, category)]
        discoveries = [
            run for run in category.get("discoveries") or [] if _valid_discovery(run, category)
        ]
        publication = category.get("publication")
        pub_ok = bool(publication and _valid_publication(publication, category))
        valid_sources += len(sources)
        valid_documents += len(documents)
        valid_discoveries += len(discoveries)
        valid_publications += int(pub_ok)
        synthetic_confirmed += sum(
            row.get("status") == "confirmed" and row.get("content_sha256") in SYNTHETIC_SHA256
            for row in provenance
        )

        if not pub_ok:
            continue
        chain = _chain(category, publication["snapshot_id"])
        root = chain.get("root_snapshot_id")
        source = next((row for row in sources if row.get("snapshot_id") == root), None)
        if source is None:
            missing.append(f"{category.get('code')}:发布快照根没有已确认真实源码")
            continue
        # 发布快照自身的祖先链才是合法来源路径；同根兄弟分支不能拼接。
        publication_chain = set(_chain_ids(chain))
        for row in documents:
            doc_chain = _chain(category, row.get("snapshot_id"))
            if not doc_chain.get("valid") or doc_chain.get("root_snapshot_id") != root:
                violations.append(f"{category.get('code')}:技术文档与源码不在同一派生链")
            elif row.get("snapshot_id") not in publication_chain:
                violations.append(f"{category.get('code')}:技术文档快照不在发布链")
        for run in discoveries:
            run_chain = _chain(category, run.get("snapshot_id"))
            if not run_chain.get("valid") or run_chain.get("root_snapshot_id") != root:
                violations.append(f"{category.get('code')}:文档发现运行与源码不在同一派生链")
            elif run.get("snapshot_id") not in publication_chain:
                violations.append(f"{category.get('code')}:文档发现快照不在发布链")
        if not documents:
            missing.append(f"{category.get('code')}:缺少已确认的真实技术文档")
        if not discoveries:
            missing.append(f"{category.get('code')}:缺少已成功的非结构文档发现")
        if (
            documents
            and discoveries
            and not any(
                item.startswith(f"{category.get('code')}:技术文档")
                or item.startswith(f"{category.get('code')}:文档发现")
                for item in violations
            )
        ):
            qualifying.append(category.get("code"))

    record(
        "source_provenance_confirmed",
        "PASS" if valid_sources else "BLOCKED",
        f"count={valid_sources}",
    )
    record(
        "document_provenance_confirmed",
        "PASS" if valid_documents else "BLOCKED",
        f"count={valid_documents}",
    )
    record(
        "semantic_discovery_succeeded",
        "PASS" if valid_discoveries else "BLOCKED",
        f"count={valid_discoveries}",
    )
    record(
        "reviewed_publication_scope_valid",
        "PASS" if valid_publications else "BLOCKED",
        f"count={valid_publications}",
    )
    record(
        "confirmed_materials_not_synthetic",
        "FAIL" if synthetic_confirmed else "PASS",
        f"confirmed_synthetic_count={synthetic_confirmed}",
    )
    same_chain = bool(qualifying) and not violations
    record(
        "same_category_and_derivation_chain",
        "PASS" if same_chain else ("FAIL" if violations else "BLOCKED"),
        f"qualifying_categories={len(qualifying)} violations={len(violations)}",
    )

    if not valid_sources:
        missing.append("没有管理员确认且可核验的真实源码来源")
    if not valid_documents:
        missing.append("没有管理员确认且可核验的真实技术文档")
    if not authorized:
        missing.append("没有与当前模型端点匹配的有效企业外发授权记录")
    if not valid_discoveries:
        missing.append("没有可核验的非结构文档发现运行")
    if not valid_publications:
        missing.append("没有审核范围可核验的真实知识发布物")

    results = [row["result"] for row in checks.values()]
    if "FAIL" in results or violations:
        result = "FAIL"
    elif all(item == "PASS" for item in results):
        result = "PASS"
    else:
        result = "BLOCKED"
    record("gate2_overall", result, "真实来源、授权、审核发布与派生链必须全部闭合")
    return {
        "result": result,
        "checks": checks,
        "missing_inputs": missing,
        "violations": violations,
        "qualifying_categories": qualifying,
        "counts": {
            "valid_sources": valid_sources,
            "valid_documents": valid_documents,
            "valid_discoveries": valid_discoveries,
            "valid_publications": valid_publications,
            "confirmed_synthetic": synthetic_confirmed,
        },
    }
