"""P2.2 术语、阈值、机制约束与否定范围的确定性工具。

这些函数只做可复核的文本/数值比较；关键词和字符二元组仅用于召回和排序，
同义改写和真正的技术等价必须由受控模型给出带证据锚点的判断，且仍由服务端校验。
"""

import re
from dataclasses import dataclass

_CN_TERM = re.compile(r"[\u4e00-\u9fff]{2,}")
_ALNUM = re.compile(r"[a-z][a-z0-9]*(?:[.\-/][a-z0-9]+)*|\d+(?:\.\d+)?", re.I)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"[。；;\n]+")
# 只剥离行尾独立序号，避免把 HTTP/1.1、HTTP/2 的版本号当成序号。
_TRAILING_NUMBER = re.compile(r"(?:[。；;]\s*|\n\s*)\d{1,4}\s*$")
_LEADING_NUMBER = re.compile(r"^\s*(?:第[一二三四五六七八九十百]+[章节条]|\d{1,4}[、.)）])\s*")
_MECHANISM = re.compile(
    r"(?:采用|基于|使用|通过|必须(?:采用|使用|基于|支持)?)[^，。；,;]{0,60}?"
    r"(?:机制|模式|算法|协议|架构|方式|模型|引擎|策略)"
)
_NEGATIVE = re.compile(
    r"不支持|不提供|不具备|未实现|不覆盖|不适用|无法支持|无法实现|无法提供|无法满足|"
    r"不能支持|不能实现|不支持该|不包含|不满足"
)
_EPISTEMIC = re.compile(r"不证明|不能证明|不保证|不代表|未核验|未验证|未知|待确认|无法确认")
_GENERIC = {
    "支持",
    "能力",
    "功能",
    "提供",
    "实现",
    "进行",
    "可以",
    "能够",
    "具备",
    "以及",
    "包括",
    "相关",
    "要求",
    "满足",
    "产品",
    "系统",
    "通过",
    "使用",
    "采用",
    "方式",
    "机制",
    "模式",
    "技术",
    "参数",
    "指标",
    "项目",
    "内容",
    "主要",
    "并且",
    "或者",
    "同时",
    "全部",
    "所有",
    "the",
    "and",
    "with",
    "for",
    "from",
    "that",
    "this",
}

# 这些是受控的技术动作组。它们只用于确定性直接支持门槛，不能把近义词当成
# 同一动作处理；例如“检测”不能替代“检测并阻断”，“监测”不能替代“自动响应”。
_ACTION_GROUPS = {
    "detect": re.compile(r"检测|探测|扫描"),
    "identify": re.compile(r"识别|辨识"),
    "block": re.compile(r"阻断|拦截|封堵|封禁"),
    "respond": re.compile(r"响应|处置|反制"),
    "monitor": re.compile(r"监测|监控|监听"),
    "parse": re.compile(r"解析"),
    "export": re.compile(r"导出"),
    "manage": re.compile(r"管理|管控"),
    "audit": re.compile(r"审计"),
    "authenticate": re.compile(r"认证|鉴权"),
    "encrypt": re.compile(r"加密|解密"),
    "log": re.compile(r"日志|记录"),
    "deploy": re.compile(r"部署"),
    "isolate": re.compile(r"隔离"),
    "backup": re.compile(r"备份|恢复"),
}

_DEPLOYMENT_GROUPS = {
    "standalone": re.compile(r"单机|单节点|单台|独立部署"),
    "dual": re.compile(r"双机|双活"),
    "hot_standby": re.compile(r"热备|主备|主从|冷备"),
    "cluster": re.compile(r"集群|多节点|分布式|负载均衡"),
    "ha": re.compile(r"高可用"),
}

_AUTOMATION_GROUPS = {
    "automatic": re.compile(r"自动|联动"),
    "manual": re.compile(r"手动|人工"),
}

_SCOPE_GROUPS = {
    "all": re.compile(r"全部|所有|任意|全量|全网"),
    "partial": re.compile(r"部分|指定|选择性"),
    "only": re.compile(r"仅|只(?:支持|能|可)"),
}

_PERFORMANCE = re.compile(r"性能|吞吐|并发|时延|带宽|容量|速率|连接数")

_METRIC_UNITS = {
    "gbps": ("rate", 1_000_000_000.0, "Gbps"),
    "mbps": ("rate", 1_000_000.0, "Mbps"),
    "kbps": ("rate", 1_000.0, "Kbps"),
    "bps": ("rate", 1.0, "bps"),
    "gbit/s": ("rate", 1_000_000_000.0, "Gbps"),
    "mbit/s": ("rate", 1_000_000.0, "Mbps"),
    "kbit/s": ("rate", 1_000.0, "Kbps"),
    "bit/s": ("rate", 1.0, "bps"),
    "gb/s": ("data_rate", 1_000_000_000.0, "GB/s"),
    "mb/s": ("data_rate", 1_000_000.0, "MB/s"),
    "kb/s": ("data_rate", 1_000.0, "KB/s"),
    "ms": ("time", 0.001, "ms"),
    "毫秒": ("time", 0.001, "ms"),
    "秒": ("time", 1.0, "s"),
    "s": ("time", 1.0, "s"),
    "分钟": ("time", 60.0, "min"),
    "min": ("time", 60.0, "min"),
    "小时": ("time", 3600.0, "h"),
    "h": ("time", 3600.0, "h"),
    "个": ("count", 1.0, "个"),
    "条": ("count", 1.0, "条"),
    "路": ("count", 1.0, "路"),
    "台": ("count", 1.0, "台"),
    "次": ("count", 1.0, "次"),
    "qps": ("qps", 1.0, "QPS"),
    "tps": ("qps", 1.0, "TPS"),
    "%": ("percent", 1.0, "%"),
    "％": ("percent", 1.0, "%"),
}

_NUMBER_UNIT = re.compile(
    r"(?<![\w./-])(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>Gbps|Mbps|Kbps|Gbit/s|Mbit/s|Kbit/s|bit/s|bps|GB/s|MB/s|KB/s|"
    r"毫秒|分钟|小时|ms|秒|s|min|h|个|条|路|台|次|QPS|qps|TPS|tps|%|％)"
    r"(?![A-Za-z0-9])",
    re.I,
)
_OPERATORS = {
    "不低于": ">=",
    "不少于": ">=",
    "至少": ">=",
    "不小于": ">=",
    "大于等于": ">=",
    "≥": ">=",
    ">=": ">=",
    "不高于": "<=",
    "不超过": "<=",
    "至多": "<=",
    "不大于": "<=",
    "小于等于": "<=",
    "≤": "<=",
    "<=": "<=",
    "高于": ">",
    "大于": ">",
    ">": ">",
    "低于": "<",
    "小于": "<",
    "<": "<",
    "等于": "=",
    "=": "=",
}
_UPPER_MARKERS = re.compile(r"最大|最高|可达|上限|峰值|不超过|不高于|至多|≤|<=|小于等于")
_DEFAULT_MARKERS = re.compile(r"默认|建议|典型|初始|出厂|测试环境|单机(?:配置|环境)?")
_CLAUSE_SPLIT = re.compile(r"[，。；;、\n]")


@dataclass
class MetricClaim:
    metric: str
    value: float
    unit: str
    family: str
    base_value: float
    operator: str
    upper_bound: bool
    default_scope: bool
    context: str


def _groups(text: str, groups: dict[str, re.Pattern]) -> set[str]:
    value = text or ""
    return {name for name, pattern in groups.items() if pattern.search(value)}


def clean_requirement(text: str) -> str:
    """去掉行首行号、行尾独立序号和多余空白，保留原文语义。"""
    value = str(text or "").replace("\u3000", " ").replace("\xa0", " ")
    value = _LEADING_NUMBER.sub("", value)
    value = _TRAILING_NUMBER.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def normalize(text: str) -> str:
    value = clean_requirement(text).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def alnum_terms(text: str) -> set[str]:
    return {
        item.casefold()
        for item in _ALNUM.findall(clean_requirement(text))
        if item.casefold() not in _GENERIC
    }


def terms(text: str) -> set[str]:
    value = clean_requirement(text)
    result = set(alnum_terms(value))
    for chunk in _CN_TERM.findall(value):
        if chunk in _GENERIC:
            continue
        if len(chunk) <= 4:
            result.add(chunk)
        for index in range(len(chunk) - 1):
            gram = chunk[index : index + 2]
            if gram not in _GENERIC:
                result.add(gram)
    return result


def coverage(requirement: str, text: str) -> float:
    needed = terms(requirement)
    if not needed:
        return 0.0
    return len(needed & terms(text)) / len(needed)


def technical_actions(text: str) -> set[str]:
    return _groups(text, _ACTION_GROUPS)


def deployment_forms(text: str) -> set[str]:
    return _groups(text, _DEPLOYMENT_GROUPS)


def automation_forms(text: str) -> set[str]:
    return _groups(text, _AUTOMATION_GROUPS)


def scope_forms(text: str) -> set[str]:
    return _groups(text, _SCOPE_GROUPS)


def direct_support(
    requirement: str,
    candidate: str,
    conditions: list[str] | None = None,
    candidate_conditions: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """确定性直接支持门槛。

    关键词和字符二元组只负责召回；真正判定直接支持时，要求技术动作、受控部署/
    自动化/范围限定词、英文数字对象和核心术语同时由候选明确覆盖。任何一项无法
    确认就不直接支持，交由受控模型论证或保持无法确认。
    """
    req = clean_requirement(requirement)
    cand = candidate or ""
    condition_text = "\n".join(str(item) for item in (candidate_conditions or []))
    if not normalize(req) or not normalize(cand):
        return False, ["empty_text"]
    reasons: list[str] = []
    req_actions = technical_actions(req)
    cand_actions = technical_actions(cand)
    missing_actions = sorted(req_actions - cand_actions)
    if missing_actions:
        reasons.append("missing_action:" + ",".join(missing_actions))
    for name, values in (
        ("deployment", deployment_forms),
        ("automation", automation_forms),
        ("scope", scope_forms),
    ):
        required = values(req)
        present = values(cand)
        missing = sorted(required - present)
        if missing:
            reasons.append(f"missing_{name}:" + ",".join(missing))
    if _PERFORMANCE.search(req) and not _PERFORMANCE.search(cand):
        reasons.append("missing_performance_scope")
    needed_alnum = alnum_terms(req)
    have_alnum = alnum_terms(cand)
    missing_alnum = sorted(needed_alnum - have_alnum)
    if missing_alnum:
        reasons.append("missing_object:" + ",".join(missing_alnum))
    needed = terms(req)
    if not needed:
        reasons.append("no_requirement_terms")
    else:
        coverage_ratio = len(needed & terms(cand)) / len(needed)
        if coverage_ratio < 0.5:
            reasons.append(f"scope_coverage:{coverage_ratio:.2f}")
    for condition in conditions or []:
        condition_terms = terms(str(condition))
        available = terms(cand) | terms(condition_text)
        if condition_terms and len(condition_terms & available) / len(condition_terms) < 0.7:
            reasons.append("condition_not_covered")
            break
    return not reasons, reasons


def negative_scope_matches(requirement: str, claim: str) -> bool:
    """否定文本必须否定同一技术动作、对象、部署/自动化形态和适用范围。"""
    req = clean_requirement(requirement)
    value = claim or ""
    if not req or not value:
        return False
    req_actions = technical_actions(req)
    claim_actions = technical_actions(value)
    if req_actions - claim_actions:
        return False
    for values in (deployment_forms, automation_forms, scope_forms):
        required = values(req)
        if required - values(value):
            return False
    if _PERFORMANCE.search(req) and not _PERFORMANCE.search(value):
        return False
    if not alnum_terms(req).issubset(alnum_terms(value)):
        return False
    needed = terms(req)
    if not needed:
        return False
    return len(needed & terms(value)) / len(needed) >= 0.6


def mechanism_required(requirement: str) -> tuple[bool, list[str]]:
    match = _MECHANISM.search(requirement or "")
    if not match:
        return False, []
    segment = match.group(0)
    found = sorted(terms(segment), key=len, reverse=True)[:8]
    return True, found


def mechanism_conflict(requirement: str, candidate: str) -> bool:
    required, required_terms = mechanism_required(requirement)
    if not required or not required_terms:
        return False
    lower = (candidate or "").casefold()
    common = [term for term in required_terms if term in lower]
    if common:
        return False
    # 只有候选明确陈述了另一种实现机制时才是机制冲突；缺证据不能判为不支持。
    return bool(re.search(r"采用|使用|基于|通过", candidate or ""))


def negative_claims(limitations: list[str]) -> list[str]:
    claims = []
    for item in limitations or []:
        for sentence in _SENTENCE.split(item):
            value = sentence.strip()
            if not value or _EPISTEMIC.search(value):
                continue
            if _NEGATIVE.search(value):
                claims.append(value)
    return claims


def scope_overlap(requirement: str, claim: str) -> float:
    needed = terms(clean_requirement(requirement))
    if not needed:
        return 0.0
    return len(needed & terms(claim)) / len(needed)


def _operator(value: str | None) -> str | None:
    text = (value or "").strip()
    if text in _OPERATORS:
        return _OPERATORS[text]
    for key, normalized in _OPERATORS.items():
        if key in text:
            return normalized
    return None


def _metric_name(prefix: str) -> str:
    value = _CLAUSE_SPLIT.split(prefix or "")[-1]
    value = re.sub(
        r"(?:不低于|不高于|不少于|不超过|至少|至多|大于等于|小于等于|不小于|不大于|"
        r"高于|大于|低于|小于|等于|≥|≤|>=|<=|>|<|=|为|是)\s*$",
        "",
        value,
    )
    value = re.sub(
        r"^(?:支持|提供|具备|实现|要求|系统|产品|可|能够|可以|在[^，。；;]{0,20}?下)\s*",
        "",
        value,
    )
    value = re.sub(r"HTTP/\d+(?:\.\d+)?|IPv\d+|TLS\d+(?:\.\d+)?|SHA-?\d+", " ", value)
    value = value.strip(" ：:，,、\t")
    value = re.sub(r"\s+", "", value)
    if value in _GENERIC or value in {"功能", "能力", "项目", "指标", "参数", "要求"}:
        return ""
    return value


def _unit(value: str) -> tuple[str, float, str] | None:
    return _METRIC_UNITS.get((value or "").casefold())


def _claims(text: str) -> list[MetricClaim]:
    claims = []
    for match in _NUMBER_UNIT.finditer(text or ""):
        unit = _unit(match.group("unit"))
        if not unit:
            continue
        family, factor, normalized_unit = unit
        start = match.start()
        prefix = (text or "")[max(0, start - 60) : start]
        clause = _CLAUSE_SPLIT.split(prefix)[-1]
        metric = _metric_name(prefix)
        if not metric:
            continue
        tail = (text or "")[start : match.end() + 12]
        context = prefix[-40:] + match.group(0)
        claims.append(
            MetricClaim(
                metric=metric,
                value=float(match.group("value")),
                unit=normalized_unit,
                family=family,
                base_value=float(match.group("value")) * factor,
                operator=_operator(clause) or "",
                upper_bound=bool(_UPPER_MARKERS.search(clause + tail)),
                default_scope=bool(_DEFAULT_MARKERS.search(clause + tail)),
                context=context,
            )
        )
    return claims


def _metric_matches(required: str, claim: str) -> bool:
    left = normalize(required)
    right = normalize(claim)
    if not left or not right:
        return False
    if left in right or right in left:
        return min(len(left), len(right)) >= 2
    left_terms = terms(required)
    right_terms = terms(claim)
    if not left_terms or not right_terms:
        return False
    common = left_terms & right_terms
    return len(common) >= 2 and len(common) / len(right_terms) >= 0.5


def _required_metric(
    text: str, parameter_name: str, threshold: str | None, unit_value: str | None
) -> dict | None:
    if not threshold:
        return None
    try:
        required_value = float(str(threshold).replace(",", ""))
    except (TypeError, ValueError):
        return None
    metric = ""
    parsed_unit = None
    operator = None
    for match in _NUMBER_UNIT.finditer(text or ""):
        if abs(float(match.group("value")) - required_value) > 1e-12:
            continue
        parsed = _unit(match.group("unit"))
        if not parsed:
            continue
        parsed_unit = parsed
        prefix = (text or "")[: match.start()]
        metric = _metric_name(prefix)
        operator = _operator(_CLAUSE_SPLIT.split(prefix)[-1])
        break
    if not metric:
        metric = _metric_name(parameter_name)
    field_unit = _unit(unit_value or "") if unit_value else None
    if parsed_unit and field_unit and parsed_unit[0] != field_unit[0]:
        return None
    chosen = field_unit or parsed_unit
    if not metric or not chosen:
        return None
    return {
        "metric": metric,
        "value": required_value,
        "family": chosen[0],
        "unit": chosen[2],
        "base_value": required_value * chosen[1],
        "operator": operator,
    }


def metric_evaluation(
    requirement_text: str,
    parameter_name: str,
    threshold: str | None,
    operator: str | None,
    unit: str | None,
    candidate_text: str,
) -> dict:
    """把阈值和候选数值共同绑定到指标、单位和条件后再比较。"""
    required = _required_metric(requirement_text, parameter_name, threshold, unit)
    if not required:
        return {
            "state": "unknown",
            "reason": "metric_or_unit_unbound",
            "required": None,
            "claim": None,
        }
    required_operator = _operator(operator) or required["operator"]
    if not required_operator:
        return {
            "state": "unknown",
            "reason": "comparison_operator_unbound",
            "required": required,
            "claim": None,
        }
    candidates = [
        claim
        for claim in _claims(candidate_text)
        if claim.family == required["family"]
        and (claim.unit == required["unit"] or required["family"] in {"rate", "data_rate", "time"})
        and _metric_matches(required["metric"], claim.metric)
    ]
    if not candidates:
        return {
            "state": "unknown",
            "reason": "candidate_metric_not_bound",
            "required": required,
            "claim": None,
        }
    usable = [claim for claim in candidates if not claim.default_scope]
    if not usable:
        return {
            "state": "unknown",
            "reason": "candidate_only_default_scope",
            "required": required,
            "claim": candidates[0],
        }
    if len(usable) > 1:
        bounded = [claim for claim in usable if claim.upper_bound]
        # 多个同指标数值时，只有唯一一个明确的产品上限可比较；两个“最大”
        # 数值互相矛盾时仍属于无法归属，不能取第一个数字猜结论。
        if len(bounded) != 1:
            return {
                "state": "unknown",
                "reason": "multiple_candidate_values",
                "required": required,
                "claim": usable[0],
            }
        usable = bounded
    claim = usable[0]
    left = claim.base_value
    right = required["base_value"]
    satisfied = {
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
        "=": abs(left - right) <= max(abs(right) * 0.01, 1e-9),
    }[required_operator]
    return {
        "state": "satisfied" if satisfied else "not_satisfied",
        "reason": "metric_compared" if satisfied else "required_value_not_met",
        "required": {**required, "operator": required_operator},
        "claim": {
            "metric": claim.metric,
            "value": claim.value,
            "unit": claim.unit,
            "operator": claim.operator,
            "upper_bound": claim.upper_bound,
            "default_scope": claim.default_scope,
            "context": claim.context[:160],
        },
    }


def quantitative_scope(text: str) -> str:
    """去掉阈值、单位和比较符，只保留指标对象/动作，用于直接支持门槛。"""
    value = _NUMBER_UNIT.sub(" ", text or "")
    value = re.sub(r"\d+(?:\.\d+)?", " ", value)
    value = re.sub(
        r"不低于|不少于|至少|不小于|大于等于|不高于|不超过|至多|不大于|"
        r"小于等于|高于|大于|低于|小于|等于|≥|≤|>=|<=|>|<|=|:",
        " ",
        value,
    )
    return value.strip(" \t\r\n。；;，,、")


_OPEN_SET = re.compile(
    r"包括但不限于|不限于|例如|比如|如(?=[：:])|任意|任选|等(?![保级待于])|或更多|至少其中"
)
_COUNT_UNIT = re.compile(r"(?:至少|最少|不少于|不低于|不小于|≥|>=)\s*\d+\s*(项|种|个|条|类|款)")
_GENERIC_OPTIONS = {
    "功能",
    "能力",
    "项目",
    "指标",
    "参数",
    "要求",
    "安全功能",
    "技术功能",
    "功能项",
}


def minimum_options(requirement: str) -> list[str]:
    value = clean_requirement(requirement)
    value = re.sub(
        r"^.*?(?:至少|最少|不少于|不低于|不小于|≥|>=)\s*\d+\s*"
        r"(?:项|种|个|条|类|款)?(?:中|的)?",
        "",
        value,
    )
    value = re.sub(r"^(?:功能|能力|项目|参数|要求)?\s*[：:]?\s*", "", value)
    value = re.sub(r"^(?:支持|包括|包含|具备|提供)", "", value).strip("：: ")
    parts = re.split(r"[、,，;；]|以及|或者|和|及|或", value)
    options = []
    for part in parts:
        item = part.strip(" 。：:")
        if len(item) < 2 or item in _GENERIC_OPTIONS:
            continue
        if item not in options:
            options.append(item)
    return options


def minimum_scope(requirement: str, options: list[str], required: int | None) -> dict:
    text = clean_requirement(requirement)
    count_unit = _COUNT_UNIT.search(text)
    open_markers = sorted(set(_OPEN_SET.findall(text)))
    complete = bool(options) and len(options) <= 20 and not open_markers and bool(count_unit)
    return {
        "count_unit": count_unit.group(1) if count_unit else None,
        "open_markers": open_markers,
        "complete_set": complete,
        "option_count": len(options),
        "required": required,
    }


def content_text(content: dict, fields: list[str] | None = None) -> str:
    values = []
    for key, value in (content or {}).items():
        if fields and key not in fields:
            continue
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return "\n".join(values)


def assertion_evidence(assertions: list[dict]) -> list[str]:
    ids = []
    seen = set()
    for assertion in assertions or []:
        for key in ("evidence_ids", "implementation_evidence_ids"):
            for eid in assertion.get(key, []):
                if eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
        for dependency in assertion.get("dependencies", []):
            for eid in dependency.get("evidence_ids", []):
                if eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
    return ids


def negative_assertion_bindings(capability: dict) -> list[dict]:
    """返回已审核的否定性断言绑定，而不是任何含“不支持”字样的引文。"""
    content = capability.get("content") or {}
    limitations = content.get("limitations") or []
    by_field = {item.get("field"): item for item in capability.get("assertions") or []}
    result = []
    seen = set()
    for index, limitation in enumerate(limitations):
        for sentence in _SENTENCE.split(limitation):
            claim = sentence.strip()
            if not claim or _EPISTEMIC.search(claim) or not _NEGATIVE.search(claim):
                continue
            field = f"limitations.{index}"
            assertion = by_field.get(field)
            if assertion and assertion.get("semantic_decision") not in (None, "supported"):
                continue
            evidence_ids = assertion_evidence([assertion]) if assertion else []
            key = (field, claim)
            if key in seen:
                continue
            seen.add(key)
            result.append({"field": field, "claim": claim, "evidence_ids": evidence_ids})
    return result


def negative_assertions(capability: dict) -> list[tuple[str, list[str]]]:
    return [
        (binding["claim"], binding["evidence_ids"])
        for binding in negative_assertion_bindings(capability)
    ]
