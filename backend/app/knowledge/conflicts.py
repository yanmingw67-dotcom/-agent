"""P1.6 双源冲突：只发现和记录，不自动合并、不自动裁决。"""

import hashlib
import re

from sqlalchemy import select

from app.db.knowledge_models import CapabilityClaim
from app.db.review_models import ConflictFinding


def normalize(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower())


def conflict_id(snapshot_id, left, right, kind):
    ids = sorted([left, right])
    raw = f"{snapshot_id}|{ids[0]}|{ids[1]}|{kind}".encode()
    return "cf-" + hashlib.sha256(raw).hexdigest()[:32]


def classify_pair(left, right):
    left_type = (left.draft_json or {}).get("claim_type")
    right_type = (right.draft_json or {}).get("claim_type")
    if left_type == "exclusion" and right_type != "exclusion":
        return "support_exclusion_conflict"
    if right_type == "exclusion" and left_type != "exclusion":
        return "support_exclusion_conflict"
    if left.source_channel != right.source_channel:
        return "possible_duplicate"
    return None


def scan(db, snapshot_id, category_id):
    """按归一化能力名检测显式矛盾与跨源重复；不推断语义等价。"""
    claims = list(
        db.scalars(
            select(CapabilityClaim)
            .where(CapabilityClaim.snapshot_id == snapshot_id)
            .order_by(CapabilityClaim.id)
        )
    )
    created = []
    for index, left in enumerate(claims):
        left_name = normalize((left.draft_json or {}).get("name") or left.name)
        if len(left_name) < 4:
            continue
        for right in claims[index + 1 :]:
            if normalize((right.draft_json or {}).get("name") or right.name) != left_name:
                continue
            kind = classify_pair(left, right)
            if not kind:
                continue
            cid = conflict_id(snapshot_id, left.id, right.id, kind)
            if db.get(ConflictFinding, cid):
                continue
            row = ConflictFinding(
                id=cid,
                category_id=category_id,
                snapshot_id=snapshot_id,
                source_claim_id=left.id,
                target_claim_id=right.id,
                conflict_type=kind,
                details_json={
                    "left_name": (left.draft_json or {}).get("name") or left.name,
                    "right_name": (right.draft_json or {}).get("name") or right.name,
                    "left_channel": left.source_channel,
                    "right_channel": right.source_channel,
                    "left_claim_type": (left.draft_json or {}).get("claim_type"),
                    "right_claim_type": (right.draft_json or {}).get("claim_type"),
                    "left_evidence_ids": (left.draft_json or {}).get("evidence_ids", []),
                    "right_evidence_ids": (right.draft_json or {}).get("evidence_ids", []),
                    "requires_human_resolution": True,
                },
                status="unresolved",
                resolution_json={},
            )
            db.add(row)
            created.append(row)
    return created
