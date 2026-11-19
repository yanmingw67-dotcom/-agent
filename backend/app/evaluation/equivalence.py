"""P2.2 术语、阈值、机制约束与否定范围的确定性工具。

这些函数只做可复核的文本/数值比较；同义改写和真正的技术等价必须由受控模型
给出带证据锚点的判断，且仍由服务端校验，不能仅凭关键词替换宣布等价。
"""

import re

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
    r"不支持|不提供|不具备|未实现|不覆盖|不适用|无法支持|无法实现|不能支持|不能实现|不支持该|不包含"
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


def clean_requirement(text: str) -> str:
    value = (text or "").strip()
    value = _TRAILING_NUMBER.sub("", value)
    value = _LEADING_NUMBER.sub("", value)
    return value.strip(" \t\r\n。；;，,、")


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def terms(text: str) -> set[str]:
    value = (text or "").casefold()
    found = {item for item in _ALNUM.findall(value) if len(item) >= 2 and item not in _GENERIC}
    for run in _CN_TERM.findall(value):
        if len(run) <= 4:
            if run not in _GENERIC:
                found.add(run)
            continue
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if gram not in _GENERIC:
                found.add(gram)
    return found


def alnum_terms(text: str) -> set[str]:
    return {
        item
        for item in _ALNUM.findall((text or "").casefold())
        if item not in _GENERIC and (len(item) >= 2 or "." in item)
    }


def coverage(requirement: str, candidate: str) -> float:
    needed = terms(requirement)
    if not needed:
        return 0.0
    return len(needed & terms(candidate)) / len(needed)


def strong_lexical_match(requirement: str, candidate: str, *, ignore_numbers: bool = False) -> bool:
    """确定性支持门槛：关键英文/数字词全部命中，中文二元组覆盖至少一半。"""
    req = clean_requirement(requirement)
    need_alnum = {
        item
        for item in alnum_terms(req)
        if not (ignore_numbers and item.replace(".", "").isdigit())
    }
    have = terms(candidate)
    if need_alnum and not need_alnum.issubset(have):
        return False
    needed = terms(req)
    if not needed:
        return False
    common = needed & have
    if not common:
        return False
    cn_needed = needed - need_alnum
    if cn_needed:
        required_cn = max(1, (len(cn_needed) + 1) // 2)
        if len(common & cn_needed) < required_cn:
            return False
    if need_alnum:
        return True
    return len(common) / len(needed) >= 0.5


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


def threshold_satisfied(threshold: str | None, operator: str | None, text: str) -> bool | None:
    if not threshold:
        return None
    try:
        required = float(str(threshold).replace(",", ""))
    except ValueError:
        return None
    numbers = []
    for item in _NUMBER.findall(text or ""):
        try:
            numbers.append(float(item))
        except ValueError:
            continue
    if not numbers:
        return None
    value = max(numbers)
    op = (operator or "不低于").strip()
    if op in {"不低于", "≥", ">=", "至少", "不小于"}:
        return value >= required
    if op in {"不高于", "≤", "<=", "至多", "不超过", "不大于"}:
        return value <= required
    if op in {"高于", ">", "大于"}:
        return value > required
    if op in {"低于", "<", "小于"}:
        return value < required
    if op in {"=", "等于"}:
        return any(number == required for number in numbers)
    return value >= required


def minimum_options(requirement: str) -> list[str]:
    value = clean_requirement(requirement)
    value = re.sub(r"^.*?至少\s*\d+\s*(?:项|种|个|条)?(?:中|的)?", "", value)
    value = re.sub(r"^(?:支持|包括|包含|具备|提供)", "", value).strip("：: ")
    parts = re.split(r"[、,，;；]|以及|或者|和|及|或", value)
    return [part.strip(" 。") for part in parts if len(part.strip(" 。")) >= 2]


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


def negative_assertions(capability: dict) -> list[tuple[str, list[str]]]:
    content = capability.get("content") or {}
    claims = negative_claims(content.get("limitations") or [])
    limitations = content.get("limitations") or []
    result = []
    for claim in claims:
        for index, limitation in enumerate(limitations):
            if claim not in limitation:
                continue
            field = f"limitations.{index}"
            evidence = []
            for assertion in capability.get("assertions") or []:
                if assertion.get("field") == field:
                    evidence = assertion_evidence([assertion])
                    break
            result.append((claim, evidence))
            break
    return result
