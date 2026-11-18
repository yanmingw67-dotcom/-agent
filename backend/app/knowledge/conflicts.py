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
    r"默认|可选|必须|需|须|要求|允许|开启|启用|打开|关闭|禁用|禁止"
    r"|参数|配置项|版本|许可|授权|部署模式|高可用"
)
SCOPE_PREFIX = re.compile(r"^(?:仅支持|只支持|仅限|只限|适用于|支持)")
SCOPE_SUFFIX = re.compile(r"(?:部署|环境|模式|方式|场景)$")
CONDITION_PREFIX = re.compile(r"^(?:必须|需|须|要求|可选|允许|支持)")
STATE_ON = {"开启", "启用", "打开", "允许"}
STATE_OFF = {"关闭", "禁用", "禁止", "不支持"}
REQUIRED = {"必须", "需", "须", "要求"}
OPTIONAL = {"可选", "允许"}
EXCLUSIVE_TERMS = [
    ("生产", "测试"),
    ("集群", "单机"),
    ("公有", "私有"),
    ("内部", "外部"),
]
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


def _state_conflict(left, right):
    left_on, right_on = left & STATE_ON, right & STATE_ON
    left_off, right_off = left & STATE_OFF, right & STATE_OFF
    if (left_on and right_off) or (right_on and left_off):
        return True
    return bool((left & REQUIRED and right & OPTIONAL) or (right & REQUIRED and left & OPTIONAL))


def _config_state_conflict(left, right):
    return _state_conflict(left["config"], right["config"])


def dimension_mismatch(left, right):
    left_dims, right_dims = _dimensions(left), _dimensions(right)
    if _disjoint(left_dims["form"], right_dims["form"]):
        return "product_form"
    if _disjoint(left_dims["component"], right_dims["component"]):
        return "component"
    left_config, right_config = left_dims["config"], right_dims["config"]
    if _disjoint(left_config, right_config):
        return "configuration"
    if _config_state_conflict(left_dims, right_dims):
        return "configuration"
    return None


def _scope_value(value):
    text = normalize(value)
    text = SCOPE_PREFIX.sub("", text)
    return SCOPE_SUFFIX.sub("", text)


def _condition_value(value):
    return CONDITION_PREFIX.sub("", normalize(value))


def _known_exclusive(left, right):
    for first, second in EXCLUSIVE_TERMS:
        if (first in left and second in right) or (second in left and first in right):
            return True
    return False


def _scope_relation(left, right):
    """只有双方均有明确范围且可证明互斥时才返回 exclusive。"""
    left_scope = {
        value
        for value in (_scope_value(item) for item in (left.get("supported_scope") or []))
        if len(value) >= 2
    }
    right_scope = {
        value
        for value in (_scope_value(item) for item in (right.get("supported_scope") or []))
        if len(value) >= 2
    }
    if not left_scope or not right_scope:
        return "unknown"
    if any(
        left_value == right_value or left_value in right_value or right_value in left_value
        for left_value in left_scope
        for right_value in right_scope
    ):
        return "overlap"
    if any(
        _known_exclusive(left_value, right_value)
        for left_value in left_scope
        for right_value in right_scope
    ):
        return "exclusive"
    return "unknown"


def _condition_relation(left, right):
    left_conditions = {
        value
        for value in (_condition_value(item) for item in (left.get("conditions") or []))
        if len(value) >= 2
    }
    right_conditions = {
        value
        for value in (_condition_value(item) for item in (right.get("conditions") or []))
        if len(value) >= 2
    }
    if not left_conditions or not right_conditions:
        return "unknown"
    if any(
        left_value == right_value or left_value in right_value or right_value in left_value
        for left_value in left_conditions
        for right_value in right_conditions
    ):
        return "overlap"
    raw_left = " ".join(str(item) for item in (left.get("conditions") or []))
    raw_right = " ".join(str(item) for item in (right.get("conditions") or []))
    if _state_conflict(set(CONFIG.findall(raw_left)), set(CONFIG.findall(raw_right))):
        return "exclusive"
    if any(
        _known_exclusive(left_value, right_value)
        for left_value in left_conditions
        for right_value in right_conditions
    ):
        return "exclusive"
    return "unknown"


def applicability_relation(left, right):
    """比较能力适用范围；unknown 时必须保留潜在冲突。"""
    left_dims, right_dims = _dimensions(left), _dimensions(right)
    if _disjoint(left_dims["form"], right_dims["form"]):
        return "exclusive"
    if _disjoint(left_dims["component"], right_dims["component"]):
        return "exclusive"
    if _config_state_conflict(left_dims, right_dims):
        return "exclusive"
    left_draft, right_draft = left.draft_json or {}, right.draft_json or {}
    scope_relation = _scope_relation(left_draft, right_draft)
    condition_relation = _condition_relation(left_draft, right_draft)
    if scope_relation == "exclusive" or condition_relation == "exclusive":
        return "exclusive"
    if scope_relation == "unknown" or condition_relation == "unknown":
        return "unknown"
    return "overlap"


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
    if applicability_relation(left, right) == "exclusive":
        return None
    if is_exclusion(left) != is_exclusion(right):
        return "support_exclusion_conflict"
    if left.source_channel != right.source_channel:
        return "possible_duplicate"
    return None


def match_pair(left, right):
    left_draft, right_draft = left.draft_json or {}, right.draft_json or {}
    left_name = left_draft.get("name") or left.name
    right_name = right_draft.get("name") or right.name
    name_score = max(similarity(left_name, right_name), similarity(left.name, right.name))
    target_score = (
        similarity(left_draft.get("target"), right_draft.get("target"))
        if left_draft.get("target") and right_draft.get("target")
        else 0.0
    )
    action_score = (
        similarity(left_draft.get("action"), right_draft.get("action"))
        if left_draft.get("action") and right_draft.get("action")
        else 0.0
    )
    score = round(max(name_score, target_score, action_score), 3)
    if score < 0.66:
        return None
    relation = applicability_relation(left, right)
    if relation == "exclusive":
        return None
    left_exclusion, right_exclusion = is_exclusion(left), is_exclusion(right)
    if left_exclusion != right_exclusion:
        kind = "support_exclusion_conflict"
    elif left.source_channel != right.source_channel:
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
            "applicability_relation": relation,
            "scope_or_condition_mismatch": relation if relation == "exclusive" else None,
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
