"""P1.6 双源冲突：只发现和记录，不自动合并、不自动裁决。

匹配顺序：术语归一化 -> 名称语义重排/字符双词相似 -> 产品形态、组件、配置条件
与适用范围对比。只输出潜在重复、等效或显式支持/排除冲突，不做自动裁决。
"""

import hashlib
import re

from sqlalchemy import select

from app.db.knowledge_models import CapabilityClaim
from app.db.review_models import ConflictFinding

EXCLUSION = re.compile(r"不支持|不提供|禁止|未支持|无法|不能|明确排除|不在支持范围")
GENERIC_PREFIX = re.compile(r"^(?:支持|提供|具备|实现|当前版本支持)")
GENERIC_SUFFIX = re.compile(r"(?:功能|能力|特性|模块|场景)$")
FORM = re.compile(
    r"硬件|软件|云原生|云端|SaaS|一体机|单机|集群|容器|虚拟机|插件|网关|代理"
    r"|客户端|服务端|控制台|命令行|浏览器|移动端"
)
COMPONENT = re.compile(
    r"管理面|数据面|控制面|控制器|管理节点|工作节点|集群节点|边缘节点"
    r"|数据库|消息队列|缓存|对象存储|驱动|组件|接口|API"
)
CONFIG = re.compile(
    r"默认|可选|必须|开启|启用|关闭|禁用|参数|配置项|版本|许可|授权|部署模式|高可用"
)
SYNONYMS = {
    "多租户": "租户",
    "租户隔离": "租户隔离",
    "双机热备": "高可用",
    "高可用部署": "高可用",
    "鉴权": "认证",
    "身份鉴别": "认证",
    "模板导入": "模板导入",
    "导入模板": "模板导入",
    "策略模板": "模板",
    "规则下发": "规则同步",
    "规则分发": "规则同步",
}


def normalize(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower())


def conflict_id(snapshot_id, left, right, kind):
    ids = sorted([left, right])
    raw = f"{snapshot_id}|{ids[0]}|{ids[1]}|{kind}".encode()
    return "cf-" + hashlib.sha256(raw).hexdigest()[:32]


def canonical_name(value):
    text = normalize(value)
    text = GENERIC_PREFIX.sub("", text)
    text = GENERIC_SUFFIX.sub("", text)
    for source, target in SYNONYMS.items():
        text = text.replace(source, target)
    return text


def grams(value):
    if len(value) <= 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def similarity(left, right):
    a, b = canonical_name(left), canonical_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        smaller, larger = sorted([len(a), len(b)])
        if smaller >= 4:
            return 0.9
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    shared = len(ga & gb)
    containment = shared / min(len(ga), len(gb))
    jaccard = shared / len(ga | gb)
    if min(len(a), len(b)) >= 4 and containment >= 0.66:
        return containment
    return jaccard


def _text(claim):
    draft = claim.draft_json or {}
    values = [
        draft.get("name") or claim.name,
        draft.get("action"),
        draft.get("target"),
        draft.get("business_goal"),
        draft.get("quote"),
        *(draft.get("supported_scope") or []),
        *(draft.get("conditions") or []),
        *(draft.get("limitations") or []),
    ]
    return " ".join(str(value) for value in values if value)


def _dimensions(claim):
    text = _text(claim)
    draft = claim.draft_json or {}
    scope = [normalize(value) for value in (draft.get("supported_scope") or []) if value]
    conditions = [normalize(value) for value in (draft.get("conditions") or []) if value]
    return {
        "form": set(FORM.findall(text)),
        "component": set(COMPONENT.findall(text)),
        "config": set(CONFIG.findall(text)),
        "scope": {value for value in scope if len(value) >= 3},
        "conditions": {value for value in conditions if len(value) >= 3},
    }


def _disjoint(left, right):
    return bool(left and right and not (left & right))


def dimension_mismatch(left, right):
    left_dims, right_dims = _dimensions(left), _dimensions(right)
    if _disjoint(left_dims["form"], right_dims["form"]):
        return "product_form"
    if _disjoint(left_dims["component"], right_dims["component"]):
        return "component"
    left_config, right_config = left_dims["config"], right_dims["config"]
    if _disjoint(left_config, right_config):
        return "configuration"
    left_on = bool(left_config & {"开启", "启用"})
    right_off = bool(right_config & {"关闭", "禁用"})
    right_on = bool(right_config & {"开启", "启用"})
    left_off = bool(left_config & {"关闭", "禁用"})
    if (left_on and right_off) or (right_on and left_off):
        return "configuration"
    if ("必须" in left_config and "可选" in right_config) or (
        "可选" in left_config and "必须" in right_config
    ):
        return "configuration"
    return None


def scope_mismatch(left, right):
    left_dims, right_dims = _dimensions(left), _dimensions(right)
    if _disjoint(left_dims["scope"], right_dims["scope"]):
        return "supported_scope"
    if _disjoint(left_dims["conditions"], right_dims["conditions"]):
        return "conditions"
    return None


def is_exclusion(claim):
    draft = claim.draft_json or {}
    if draft.get("claim_type") == "exclusion":
        return True
    values = [draft.get("name") or claim.name, draft.get("action"), draft.get("target")]
    return any(EXCLUSION.search(str(value)) for value in values if value)


def classify_pair(left, right):
    """保留旧调用入口；返回冲突类型或 None，不产生副作用。"""
    if (
        similarity(left.name, right.name) < 0.66
        and similarity(
            (left.draft_json or {}).get("name") or left.name,
            (right.draft_json or {}).get("name") or right.name,
        )
        < 0.66
    ):
        return None
    if dimension_mismatch(left, right):
        return None
    if is_exclusion(left) != is_exclusion(right):
        return "support_exclusion_conflict"
    if left.source_channel != right.source_channel:
        return None if scope_mismatch(left, right) else "possible_duplicate"
    return None


def match_pair(left, right):
    left_name = (left.draft_json or {}).get("name") or left.name
    right_name = (right.draft_json or {}).get("name") or right.name
    score = round(max(similarity(left_name, right_name), similarity(left.name, right.name)), 3)
    if score < 0.66:
        return None
    mismatch = dimension_mismatch(left, right)
    if mismatch:
        return None
    left_exclusion, right_exclusion = is_exclusion(left), is_exclusion(right)
    if left_exclusion != right_exclusion:
        kind = "support_exclusion_conflict"
    elif left.source_channel != right.source_channel:
        if scope_mismatch(left, right):
            return None
        kind = "possible_duplicate"
    else:
        return None
    details = {
        "left_name": left_name,
        "right_name": right_name,
        "left_channel": left.source_channel,
        "right_channel": right.source_channel,
        "left_claim_type": (left.draft_json or {}).get("claim_type"),
        "right_claim_type": (right.draft_json or {}).get("claim_type"),
        "left_evidence_ids": (left.draft_json or {}).get("evidence_ids", []),
        "right_evidence_ids": (right.draft_json or {}).get("evidence_ids", []),
        "requires_human_resolution": True,
        "risk": "high" if kind == "support_exclusion_conflict" else "medium",
        "match": {
            "similarity": score,
            "method": "normalized_name+char_bigram+dimension_compare",
            "dimensions": {
                "left": {key: sorted(value) for key, value in _dimensions(left).items()},
                "right": {key: sorted(value) for key, value in _dimensions(right).items()},
            },
            "scope_or_condition_mismatch": scope_mismatch(left, right),
            "auto_merge": False,
            "auto_resolve": False,
        },
    }
    return kind, details


def scan(db, snapshot_id, category_id):
    """按归一化与语义重排检测潜在重复、等效及显式支持/排除冲突。"""
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
            matched = match_pair(left, right)
            if not matched:
                continue
            kind, details = matched
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
                details_json=details,
                status="unresolved",
                resolution_json={},
            )
            db.add(row)
            created.append(row)
    return created
