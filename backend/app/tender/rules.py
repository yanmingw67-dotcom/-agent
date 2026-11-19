"""P2.1 招标参数边界、噪声过滤与原子子要求。

本模块只做结构化解释，不判断产品是否支持。顶层参数数量由结构决定：
表格数据行/编号条款是顶层；内部 (1)(2)(3) 或协议枚举只生成子要求。
"""

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

from app.tender.structure import ParsedDocument, Region

TOP_NUMBER = re.compile(r"^\s*(?:第\s*)?(\d{1,3})\s*(?:条|[、.．])\s*(.*)$", re.S)
BARE_NUMBER = re.compile(r"^\s*(\d{1,3})\s*[)）]\s*(.*)$", re.S)
BULLET = re.compile(r"^\s*[-*•·]\s+(.*)$")
SUBCLAUSE = re.compile(
    r"(?:^|[\n。；;：:])\s*(?:[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[)）]|[①-⑳]|(?:\d+)[)）])\s*"
)
HEADING = re.compile(r"^(?:#{1,6}\s+|第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、)")
PROOF = re.compile(
    r"(?:^|[。；;\n]|\d{1,3}\s*[、.．)])\s*(?:应|须|需|要求)?\s*"
    r"(?:提供|提交|出具|附上|加盖|附)[^。；;\n]{0,30}"
    r"(?:检测报告|测试报告|证明材料|截图|证书|承诺函|授权函)"
    r"|证明材料要求|功能截图要求|检测报告要求|资质证书要求",
    re.I,
)
PROOF_EXPORT = re.compile(
    r"支持.{0,12}(?:导出|生成|查看|管理).{0,12}(?:PDF|Excel|Word|日志|报告|截图)", re.I
)
_PROOF_CLAUSE = re.compile(
    r"(?:(?:[，,；;。]|\n)\s*)?"
    r"(?:并且?|且|同时|另外|以及)?\s*"
    r"(?:(?:须|需|应(?:当)?|要求)\s*)?"
    r"(?:提供|提交|出具|附上|加盖|附)\s*"
    r"[^，。；;\n]{0,80}?"
    r"(?:检测报告|测试报告|证明材料|功能截图|截图|证书|承诺函|授权函)"
    r"[^，。；;\n]*"
    r"|(?:证明材料要求|功能截图要求|检测报告要求|资质证书要求)",
    re.I,
)
_EXPORT_FUNCTION = re.compile(
    r"(?:支持|具备|提供|实现|能够|可以).{0,20}(?:导出|生成|查看|管理)"
    r".{0,20}(?:PDF|Excel|Word|日志|报表|报告|截图)"
    r"|(?:支持|具备|提供|实现|能够|可以).{0,20}(?:PDF|Excel|Word|日志|报表|报告|截图)"
    r".{0,20}(?:导出|生成|查看|管理)",
    re.I,
)
_TECH_ACTION = re.compile(
    r"支持|具备|实现|能够|可以|采用|集成|兼容|覆盖|检测|阻断|防护|审计|隔离"
    r"|部署|接入|解析|管理|导出|告警|联动|认证|鉴权|加密|同步|记录"
    r"|不低于|不高于|不少于|不超过|至少|至多|阈值|吞吐|并发|时延|容量|≥|≤|>|<",
    re.I,
)
_UNCERTAIN_MATERIAL = re.compile(
    r"相关材料|附件|资料|佐证|证明材料|功能截图|检测报告|截图|报告", re.I
)
COMMERCIAL = re.compile(
    r"(?:提供|承诺|响应|实行|包含).{0,24}(?:售后|质保|保修|培训|服务)"
    r"|售后服务(?:条款|要求|承诺|响应)|原厂技术支持|原厂服务|报价|折扣|付款"
    r"|商务条款|服务与商务|交付周期|响应时间\s*\d+\s*小时|SLA",
    re.I,
)
COMPETITOR = re.compile(r"友商|竞品|控标|对比品牌|其他厂商|某品牌", re.I)
PREAMBLE = re.compile(
    r"编制说明|使用说明|填写指南|填写说明|注意事项|招标文件|投标人须知|评标办法"
    r"|目录|页码|本表说明|本表由|投标人填写|说明：",
    re.I,
)
NOISE_SECTION = re.compile(
    r"编制说明|使用说明|填写指南|填写说明|注意事项|商务|服务与商务|售后|质保|投标人须知"
    r"|评标|报价|合同|付款|证明材料|资质|承诺函|目录|备注|表格说明|友商|竞品",
    re.I,
)
TECH_SECTION = re.compile(r"技术参数|技术要求|功能参数|技术规格|参数表|招标参数", re.I)
SECTION_TITLE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节]\s*)?"
    r"(?:技术参数|技术要求|技术规格|功能要求|参数要求|招标参数|功能参数)"
    r"(?:要求|表|说明|一览表)?$",
    re.I,
)
CAPABILITY = re.compile(
    r"支持|具备|提供|实现|能够|可以|采用|集成|兼容|覆盖|检测|阻断|防护|审计|隔离"
    r"|部署|接入|解析|管理|导出|告警|联动|认证|鉴权|加密|同步|记录",
    re.I,
)
TECH_SIGNAL = re.compile(
    r"支持|具备|提供|实现|能够|可以|采用|集成|兼容|覆盖|检测|阻断|防护|审计|隔离"
    r"|部署|接入|解析|管理|导出|告警|联动|认证|鉴权|加密|同步|记录|不低于|不高于"
    r"|不少于|不超过|至少|至多|阈值|吞吐|并发|时延|容量|≥|≤|>|<",
    re.I,
)
TITLE_LINE = re.compile(r"招标|投标|技术参数表|技术规格书|tender|technical\s+parameter", re.I)
PAGE_NUMBER = re.compile(
    r"^(?:第?\s*\d+\s*页(?:\s*/?\s*共?\s*\d+\s*页)?|page\s*\d+(?:\s*of\s*\d+)?)$",
    re.I,
)
REMARK = re.compile(r"^\s*备注\s*[:：]")
CONDITION = re.compile(r"(?:在|当|仅在|限于)([^，。；;]{2,40}?)(?:下|时|情况下)")
THRESHOLD = re.compile(
    r"(不低于|不少于|不小于|不高于|不超过|不大于|"
    r"至少(?!包括|具备|支持|提供|包含|覆盖)|至多|>=|<=|≥|≤|＞|＜|>|<)\s*"
    r"(?:每(?:周|日|天|月|年)\s*)?"
    r"([0-9]+(?:\.[0-9]+)?|[_＿]{2,})?\s*"
    r"(Gbps|Mbps|MB|GB|TB|QPS|TPS|ms|毫秒|秒|分钟|小时|天|个月|年|条|个|种|路|台|套|次|%|vCPU|核|万)?",
    re.I,
)
_UNIT = r"(Gbps|Mbps|MB|GB|TB|QPS|TPS|ms|毫秒|秒|分钟|小时|天|个月|年|条|个|种|路|台|套|次|%|vCPU|核|万)"
BLANK_QUANTITY = re.compile(r"([_＿]{2,})\s*" + _UNIT, re.I)
_QUANTITY_SEPARATOR = re.compile(r"\s*(?:/|／|、|，|,|；|;|且|并且|同时|以及)\s*")


@dataclass
class Candidate:
    parameter_name: str
    original_number: str | None
    original_text: str
    normalized_text: str
    source_locator: dict
    section: str = ""
    extraction_method: str = "structural"
    extraction_confidence: str = "high"
    review_required: bool = False
    atomic_requirements: list[dict] = field(default_factory=list)
    logic_tree: dict = field(default_factory=dict)
    extra_locators: list[dict] = field(default_factory=list)
    model_hints: list[dict] = field(default_factory=list)


@dataclass
class CoverageRecord:
    disposition: str
    region_kind: str
    original_text: str
    source_locator: dict
    reason_code: str | None = None
    reason: str | None = None
    confidence: str | None = None
    parameter: Candidate | None = None


@dataclass
class ExtractionResult:
    candidates: list[Candidate]
    coverage: list[CoverageRecord]
    details: dict
    gaps: list[dict]
    skipped_count: int
    ambiguous_count: int
    coverage_status: str
    failure_code: str | None = None
    failure_reason: str | None = None


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _strip_number(text: str) -> tuple[str | None, str]:
    match = TOP_NUMBER.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    bullet = BULLET.match(text)
    if bullet:
        return None, bullet.group(1).strip()
    return None, text.strip()


def _is_heading(region: Region) -> bool:
    return region.kind == "heading" or bool(HEADING.match(region.text.strip()))


def _section_noise(section: str) -> bool:
    return bool(section and NOISE_SECTION.search(section) and not TECH_SECTION.search(section))


def _noise_reason(text: str, section: str) -> tuple[str, str] | None:
    value = (text or "").strip()
    if not value:
        return "empty_region", "空白区域"
    if PAGE_NUMBER.match(value):
        return "page_header_footer", "页眉、页脚或页码"
    if REMARK.match(value):
        return "remark", "备注内容不作为技术参数"
    if TITLE_LINE.search(value) and not TECH_SIGNAL.search(value):
        return "document_title", "文件标题或表头说明"
    if SECTION_TITLE.match(value) and not CAPABILITY.search(value):
        return "section_heading", "技术参数章节标题"
    if _section_noise(section):
        return "non_technical_section", f"章节不属于技术参数范围：{section[:80]}"
    if PREAMBLE.search(value) and not CAPABILITY.search(value):
        return "preamble_or_instruction", "编制说明、填写指南或注意事项"
    if PROOF.search(value) and not PROOF_EXPORT.search(value) and not _TECH_ACTION.search(value):
        return "proof_material", "证明材料、截图、检测报告或证书要求"
    if COMMERCIAL.search(value):
        return "commercial_terms", "售后服务、质保、培训、报价或付款条款"
    if COMPETITOR.search(value):
        return "competitor_content", "友商或竞品对比内容"
    return None


def _strip_proof(value: str):
    """只移除明确的证明材料子句，保留同一参数中的技术条件和原文位置。"""
    text = value or ""
    removed = []
    parts = []
    cursor = 0
    for match in _PROOF_CLAUSE.finditer(text):
        fragment = match.group(0)
        if _EXPORT_FUNCTION.search(fragment) or PROOF_EXPORT.search(fragment):
            continue
        start, end = match.span()
        removed.append({"text": fragment, "start": start, "end": end})
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    cleaned = "".join(parts)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.strip(" \t\r\n，,；;")
    return cleaned, removed


def _proof_needs_review(cleaned: str, removed: list[dict]):
    if not removed or not cleaned:
        return False
    return bool(_UNCERTAIN_MATERIAL.search(cleaned) or not _TECH_ACTION.search(cleaned))


def _name_from_text(text: str, number: str | None = None) -> str:
    value = _strip_number(text)[1]
    value = re.split(r"[。；;\n]", value, maxsplit=1)[0].strip()
    value = re.sub(r"^[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[)）]\s*", "", value)
    value = re.sub(r"^(?:支持|具备|提供|实现|能够|可以)\s*", "", value)
    if len(value) > 120:
        value = value[:120].rstrip("，,、；;")
    return value or (f"参数 {number}" if number else "未命名参数")


def _table_cell(cells: list[str], roles: list[str], role: str) -> str:
    values = [
        (value or "").strip()
        for value, cell_role in zip(cells, roles, strict=False)
        if cell_role == role and (value or "").strip()
    ]
    return "；".join(values)


def _table_name(cells, roles, technical):
    name = _table_cell(cells, roles, "name")
    if name:
        return _name_from_text(name)
    return _name_from_text(technical)


def _conditions(text):
    return [match.group(1).strip() for match in CONDITION.finditer(text)]


def _threshold(text):
    match = THRESHOLD.search(text)
    if not match:
        blank = BLANK_QUANTITY.search(text)
        if blank:
            return None, blank.group(2), "blank_threshold"
        return None, None, None
    operator, value, unit = match.groups()
    if not value or set(value) <= {"_", "＿"}:
        return None, unit, "blank_threshold"
    return value, unit, None


def _quantities(text: str):
    """返回文本中每个独立数值约束（含无运算符的第二个空白阈值）。"""
    found = []
    for match in THRESHOLD.finditer(text):
        operator, value, unit = match.groups()
        if not value and not unit:
            continue
        found.append({"start": match.start(), "end": match.end(), "operator": operator or None})
    for match in BLANK_QUANTITY.finditer(text):
        if any(match.start() < item["end"] and match.end() > item["start"] for item in found):
            continue
        found.append({"start": match.start(), "end": match.end(), "operator": None})
    found.sort(key=lambda item: (item["start"], item["end"]))
    return found


def _trim_quantified_segment(value: str, start: int, end: int):
    """去掉片段边缘空白、标点和片段内不成对的括号，保持连续原文。"""
    while start < end and value[start] in _TRIM:
        start += 1
    while end > start and value[end - 1] in _TRIM:
        end -= 1
    while end > start and value[end - 1] in ")）":
        fragment = value[start:end]
        if fragment.count(")") + fragment.count("）") <= fragment.count("(") + fragment.count("（"):
            break
        end -= 1
        while end > start and value[end - 1] in _TRIM:
            end -= 1
    while start < end and value[start] in "(（":
        fragment = value[start:end]
        if fragment.count("(") + fragment.count("（") <= fragment.count(")") + fragment.count("）"):
            break
        start += 1
        while start < end and value[start] in _TRIM:
            start += 1
    return value[start:end], start, end


def _quantified_parts(value: str):
    """把一条原子要求中的多个量化约束拆成 AND 子叶；不可安全拆分时返回 None。"""
    quantities = _quantities(value)
    if len(quantities) < 2:
        return None
    cuts = []
    for previous, following in zip(quantities, quantities[1:], strict=False):
        separator = None
        for match in _QUANTITY_SEPARATOR.finditer(value, previous["end"], following["start"]):
            separator = match
        if separator is None:
            return None
        cuts.append((separator.start(), separator.end()))
    parts = []
    cursor = 0
    last_operator = None
    for index, quantity in enumerate(quantities):
        end = cuts[index][0] if index < len(cuts) else len(value)
        segment, start, end = _trim_quantified_segment(value, cursor, end)
        if not segment:
            return None
        operator = quantity.get("operator") or last_operator
        if quantity.get("operator"):
            last_operator = quantity["operator"]
        parts.append((segment, start, end, operator))
        cursor = cuts[index][1] if index < len(cuts) else len(value)
    return parts


def _ordered_atoms(text: str, base: int = 0):
    matches = list(SUBCLAUSE.finditer(text))
    if len(matches) < 2:
        return []
    atoms = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[match.end() : end]
        atom = raw.strip(" \n；;，,")
        if atom:
            leading = raw.find(atom)
            start = match.end() + leading
            atoms.append((atom, base + start, base + start + len(atom)))
    return atoms


def _enumerated_atoms(text: str, base: int = 0):
    body_match = re.search(r"(?:支持|具备|提供|覆盖|兼容|包含)(.*)", text, re.S)
    if not body_match:
        return []
    body = body_match.group(1).strip(" 。；;")
    parts = re.split(r"\s*[、]\s*", body)
    expanded = []
    for part in parts:
        expanded.extend(re.split(r"\s*(?:和|及|以及)\s*", part))
    parts = expanded
    if len(parts) < 2:
        return []
    if any(len(part.strip(" ，,。；;")) < 3 for part in parts):
        return []
    atoms = []
    cursor = 0
    for part in parts:
        value = part.strip(" ，,。；;")
        if not value:
            continue
        start = text.find(value, cursor)
        if start < 0:
            start = cursor
        atoms.append((value, base + start, base + start + len(value)))
        cursor = start + len(value)
    return atoms


_MINIMUM = re.compile(
    r"(?:至少|不少于|不小于|最少)\s*(?:支持|具备|提供|包含|覆盖)?\s*"
    r"([一二三四五六七八九十\d]+)\s*(种|项|个(?!月)|路)"
)
_OPEN_ENUMERATION = re.compile(r"包括但不限于|包括但不局限于|包含但不限于|至少包括", re.I)
_OPTIONAL = re.compile(r"可选|可选项|宜|建议|允许|非必须|不强制", re.I)
_REQUIRED = re.compile(r"必须|须|应(?:当)?|要求|不得|禁止", re.I)
_CAPABILITY_PREFIX = re.compile(r"^(支持|具备|提供|实现|能够|可以|覆盖|兼容|包含)(.+)$", re.S)
_OR_SPLIT = re.compile(r"\s*(?:或者|或)\s*")
_AND_SPLIT = re.compile(r"\s*(?:并且|而且|以及|和|及|、|且|[；;，,])\s*")
_TRIM = " \t\n\r；;，,。"
LOGIC_AMBIGUITY = "logic_ambiguity"
_ENUM_REQUIRED = re.compile(
    r"(?:全部|所有|均|逐项|每项|各项|都要|必须|须|应(?:当)?)"
    r".{0,16}(?:具备|满足|支持|响应|提供|包括|符合|执行)"
    r"|(?:以上|上述|所有|全部).{0,8}(?:全部|均|均须|均需|都要)",
    re.I,
)
_ENUM_EXAMPLE = re.compile(r"例如|举例|示例|譬如|(?<!同)等(?:[。；;]|$)", re.I)
_ENUM_QUALIFIER_ONLY = re.compile(
    r"^(?:以上|上述|所有|全部|各项|项|均|都|均须|均需|必须|须|需|应|应当|具备|满足|支持|提供|包括|符合|响应|执行|为|是|的|且|并|同时|以及|另外|[，,；;、\s])+$",
    re.I,
)


def _trim_span(text: str, start: int, end: int):
    while start < end and text[start] in _TRIM:
        start += 1
    while end > start and text[end - 1] in _TRIM:
        end -= 1
    return text[start:end], start, end


def _depth_at(text: str):
    depths = []
    depth = 0
    for char in text:
        depths.append(depth)
        if char in "(（":
            depth += 1
        elif char in ")）":
            depth = max(0, depth - 1)
    return depths


def _split_top(text: str, pattern):
    """只在括号外拆分；保留每个操作数在原文中的绝对锚点。"""
    depths = _depth_at(text)
    parts = []
    last = 0
    for match in pattern.finditer(text):
        if depths[match.start()] != 0:
            continue
        value, start, end = _trim_span(text, last, match.start())
        if value:
            parts.append((value, start, end))
        last = match.end()
    value, start, end = _trim_span(text, last, len(text))
    if value:
        parts.append((value, start, end))
    return parts


def _wrapped(text: str):
    value, start, end = _trim_span(text, 0, len(text))
    if len(value) < 2 or value[0] not in "(（" or value[-1] not in ")）":
        return None
    depth = 0
    for index, char in enumerate(value):
        if char in "(（":
            depth += 1
        elif char in ")）":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return None
    if depth != 0:
        return None
    return value[1:-1], start + 1, start + len(value) - 1


def _enum_mode(value: str):
    if _ENUM_REQUIRED.search(value):
        return "required"
    if _ENUM_EXAMPLE.search(value):
        return "example"
    return "uncertain"


def _mandatory(value: str, open_enum: bool = False):
    if open_enum:
        return False
    if _REQUIRED.search(value):
        return True
    return not bool(_OPTIONAL.search(value))


def _leaf_node(
    value: str,
    start: int,
    end: int,
    *,
    group_id: str,
    relation: str,
    explicit: bool = False,
    open_enum: bool = False,
    open_enum_mode: str = "uncertain",
    logic_ambiguity: str | None = None,
    minimum=None,
    operator: str | None = None,
):
    raw_start = start
    value, local_start, local_end = _trim_span(value, 0, len(value))
    if not value:
        return None
    start, end = raw_start + local_start, raw_start + local_end
    threshold, unit, threshold_ambiguity = _threshold(value)
    operator_match = THRESHOLD.search(value)
    threshold_operator = operator_match.group(1) if operator_match else operator
    if minimum:
        threshold = minimum.group(1)
        unit = minimum.group(2)
        threshold_ambiguity = None
    if not threshold and not threshold_ambiguity and re.search(r"[_＿]{2,}", value):
        threshold_ambiguity = "blank_threshold"
    ambiguity = threshold_ambiguity
    if open_enum and not ambiguity:
        ambiguity = "open_enumeration"
    if logic_ambiguity and not ambiguity:
        ambiguity = logic_ambiguity
    if minimum:
        requirement_type, relation_code = "minimum_count", "AT_LEAST"
    elif explicit:
        requirement_type, relation_code = "explicit_subclause", relation
    elif open_enum:
        requirement_type, relation_code = "open_enumeration", relation
    elif relation == "OR":
        requirement_type, relation_code = "alternative", relation
    else:
        requirement_type, relation_code = "capability", relation
    mandatory = _mandatory(value, open_enum)
    if open_enum:
        mandatory = open_enum_mode == "required"
    requirement = {
        "requirement_id": uuid.uuid4().hex,
        "original_text": value,
        "requirement_type": requirement_type,
        "logical_relation": relation_code,
        "logical_group_id": group_id,
        "is_mandatory": mandatory,
        "mandatory_status": (
            open_enum_mode if open_enum else ("required" if mandatory else "optional")
        ),
        "open_enumeration_mode": open_enum_mode if open_enum else None,
        "example_relation": bool(open_enum and open_enum_mode == "example"),
        "threshold": threshold,
        "threshold_operator": threshold_operator,
        "unit": unit,
        "conditions": _conditions(value),
        "ambiguity": ambiguity,
        "logic_ambiguity": logic_ambiguity,
        "source_span": {"start": start, "end": end},
        "parent_requirement_id": None,
        "depth": 0,
    }
    node = {
        "kind": "leaf",
        "requirement_id": requirement["requirement_id"],
        "relation": "LEAF",
        "logical_relation": relation_code,
        "logical_group_id": group_id,
        "source_span": requirement["source_span"],
        "children": [],
    }
    return {"requirements": [requirement], "node": node, "relation": relation_code}


def _compose(parts, operator: str, start: int, end: int, group_id: str):
    parts = [part for part in parts if part]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    children = [part["node"] for part in parts]
    requirements = [req for part in parts for req in part["requirements"]]
    node = {
        "kind": "node",
        "node_id": uuid.uuid4().hex,
        "relation": operator,
        "logical_relation": operator,
        "logical_group_id": group_id,
        "source_span": {"start": start, "end": end},
        "children": children,
    }
    return {"requirements": requirements, "node": node, "relation": operator}


def _parse_logic(
    text: str,
    base: int,
    group_id: str,
    *,
    explicit: bool = False,
    open_enum: bool = False,
    open_enum_mode: str = "uncertain",
    relation: str = "AND",
    inherited_operator: str | None = None,
):
    value, start, end = _trim_span(text, 0, len(text))
    if not value:
        return None
    if _OPEN_ENUMERATION.search(value):
        open_enum = True
        detected_mode = _enum_mode(value)
        # 父级已判定“全部必要/示例”时，子片段不能因缺少限定词退回不确定。
        if detected_mode != "uncertain" or open_enum_mode == "uncertain":
            open_enum_mode = detected_mode
    if value.count("(") + value.count("（") != value.count(")") + value.count("）"):
        return _leaf_node(
            value,
            base + start,
            base + end,
            group_id=group_id,
            relation="UNKNOWN",
            explicit=explicit,
            open_enum=open_enum,
            open_enum_mode=open_enum_mode,
            logic_ambiguity="unbalanced_parentheses",
        )

    # 显式 (1)(2)(3) 子条款是 AND 组；每个子条款仍可递归解析自身逻辑。
    subclauses = _ordered_atoms(value, base + start)
    if len(subclauses) >= 2:
        parts = []
        for child, child_start, _child_end in subclauses:
            parts.append(
                _parse_logic(
                    child,
                    child_start,
                    group_id,
                    explicit=True,
                    open_enum=open_enum,
                    open_enum_mode=open_enum_mode,
                )
            )
        return _compose(parts, "AND", base + start, base + end, group_id)

    minimum = _MINIMUM.search(value)
    if minimum:
        before_minimum = value[: minimum.start()]
        minimum_is_count = minimum.group(2) in {"种", "项", "路"} or bool(
            re.search(r"支持|具备|提供|包含|覆盖", before_minimum)
        )
        if minimum_is_count and not re.search(
            r"或者|或|并且|而且|以及|和|及|且|[；;，,]", before_minimum
        ):
            return _leaf_node(
                value,
                base + start,
                base + end,
                group_id=group_id,
                relation="AT_LEAST",
                explicit=explicit,
                open_enum=open_enum,
                open_enum_mode=open_enum_mode,
                minimum=minimum,
                operator=inherited_operator,
            )

    prefix = _CAPABILITY_PREFIX.match(value)
    if prefix:
        prefix_text, rest = prefix.group(1), prefix.group(2)
        parsed = _parse_logic(
            rest,
            base + start + len(prefix_text),
            group_id,
            explicit=explicit,
            open_enum=open_enum,
            open_enum_mode=open_enum_mode,
            relation=relation,
            inherited_operator=inherited_operator,
        )
        if parsed and parsed["requirements"]:
            first = parsed["requirements"][0]
            rest_start = base + start + len(prefix_text)
            leading = len(rest) - len(rest.lstrip())
            content_start = rest_start + leading
            # 前缀只能贴到连续原文上；括号内的子要求不能拼成不存在的原文。
            if first["source_span"]["start"] == content_start:
                first["original_text"] = value[: first["source_span"]["end"] - base]
                first["source_span"]["start"] = base + start
            parsed["node"]["source_span"]["start"] = base + start
            return parsed

    or_parts = _split_top(value, _OR_SPLIT)
    if len(or_parts) >= 2 and all(len(part[0].strip(" ，,。；;")) >= 1 for part in or_parts):
        parts = [
            _parse_logic(
                part,
                base + start + part_start,
                group_id,
                open_enum=open_enum,
                open_enum_mode=open_enum_mode,
                relation="OR",
                inherited_operator=inherited_operator,
            )
            for part, part_start, _ in or_parts
            if not (open_enum and _ENUM_QUALIFIER_ONLY.match(part[0].strip(" ，,。；;")))
        ]
        return _compose(
            parts,
            "OR_OPEN" if open_enum else "OR",
            base + start,
            base + end,
            group_id,
        )

    and_parts = _split_top(value, _AND_SPLIT)
    if len(and_parts) >= 2 and all(len(part[0].strip(" ，,。；;")) >= 1 for part in and_parts):
        parts = [
            _parse_logic(
                part,
                base + start + part_start,
                group_id,
                open_enum=open_enum,
                open_enum_mode=open_enum_mode,
                relation="AND",
                inherited_operator=inherited_operator,
            )
            for part, part_start, _ in and_parts
            if not (open_enum and _ENUM_QUALIFIER_ONLY.match(part[0].strip(" ，,。；;")))
        ]
        return _compose(
            parts,
            "AND_OPEN" if open_enum else "AND",
            base + start,
            base + end,
            group_id,
        )

    wrapped = _wrapped(value)
    if wrapped:
        inner, inner_start, inner_end = wrapped
        parsed = _parse_logic(
            inner,
            base + inner_start,
            group_id,
            explicit=explicit,
            open_enum=open_enum,
            open_enum_mode=open_enum_mode,
            inherited_operator=inherited_operator,
        )
        if parsed:
            parsed["node"]["source_span"] = {"start": base + start, "end": base + end}
            return parsed

    quantified = _quantified_parts(value)
    if quantified:
        parts = []
        for segment, segment_start, _segment_end, segment_operator in quantified:
            parts.append(
                _parse_logic(
                    segment,
                    base + start + segment_start,
                    group_id,
                    explicit=explicit,
                    open_enum=open_enum,
                    open_enum_mode=open_enum_mode,
                    relation=relation,
                    inherited_operator=segment_operator,
                )
            )
        if all(parts):
            return _compose(
                parts,
                # 同一条原子要求内的多个量化约束是并列的硬条件，不能因外层是 OR
                # 就把它们降级为“任一满足”。每个片段内部的 OR 仍由递归解析处理。
                "AND",
                base + start,
                base + end,
                group_id,
            )

    return _leaf_node(
        value,
        base + start,
        base + end,
        group_id=group_id,
        relation=relation,
        explicit=explicit,
        open_enum=open_enum,
        open_enum_mode=open_enum_mode,
        operator=inherited_operator,
    )


def _assign_tree(requirements, node, parent=None, depth=0):
    if node.get("kind") == "leaf":
        requirement = next(
            item for item in requirements if item["requirement_id"] == node["requirement_id"]
        )
        requirement["parent_requirement_id"] = parent
        requirement["depth"] = depth
        return
    node_id = node.get("node_id")
    for child in node.get("children", []):
        _assign_tree(requirements, child, node_id, depth + 1)


def _atomic(text: str, base: int = 0):
    group_id = uuid.uuid4().hex
    parsed = _parse_logic(text, base, group_id)
    return parsed["requirements"] if parsed else []


def build_requirements(text: str):
    group_id = uuid.uuid4().hex
    parsed = _parse_logic(text, 0, group_id)
    if not parsed:
        requirements = []
        tree = {"relation": "AND", "logical_group_id": group_id, "children": []}
        return requirements, tree
    requirements = parsed["requirements"]
    tree = parsed["node"]
    _assign_tree(requirements, tree)
    tree = {
        **tree,
        "relation": tree.get("relation") or tree.get("logical_relation") or "AND",
        "logical_group_id": group_id,
    }
    return requirements, tree


def _shift_spans(node: dict, offset: int):
    span = node.get("source_span")
    if span:
        span["start"] += offset
        span["end"] += offset
    for child in node.get("children", []):
        _shift_spans(child, offset)


def _merge_continuation(previous: Candidate, candidate: Candidate, separator: str = "\n"):
    """并入续行时保留已校验锚点，只平移新增子要求。"""
    offset = len(previous.original_text) + len(separator)
    previous.original_text = previous.original_text + separator + candidate.original_text
    previous.normalized_text = normalize(previous.original_text)
    _shift_spans(candidate.logic_tree, offset)
    previous.atomic_requirements.extend(candidate.atomic_requirements)
    if not previous.logic_tree:
        previous.logic_tree = candidate.logic_tree
    else:
        previous.logic_tree = {
            "kind": "node",
            "node_id": uuid.uuid4().hex,
            "relation": "AND",
            "logical_relation": "AND",
            "logical_group_id": previous.logic_tree.get("logical_group_id")
            or candidate.logic_tree.get("logical_group_id"),
            "source_span": {"start": 0, "end": len(previous.original_text)},
            "children": [previous.logic_tree, candidate.logic_tree],
        }
    for requirement in previous.atomic_requirements:
        requirement["depth"] = requirement.get("depth", 0) + 1
    previous.review_required = previous.review_required or candidate.review_required
    return previous


def _candidate_from_text(
    text: str,
    locator: dict,
    *,
    section: str,
    number: str | None,
    method: str = "structural",
    confidence: str = "high",
    review_required: bool = False,
    extra_locators: list[dict] | None = None,
    requirement_body: str | None = None,
    requirement_offset: int | None = None,
):
    anchor = (text or "").strip()
    clean = normalize(anchor)
    name = _name_from_text(anchor, number)
    if requirement_body is not None:
        body = requirement_body.strip()
        offset = requirement_offset if requirement_offset is not None else anchor.find(body)
    elif number:
        body = _strip_number(anchor)[1]
        offset = anchor.find(body) if body else 0
    else:
        body = anchor
        offset = 0
    # 绝不为通过测试而虚构偏移：锚点必须逐字落在参数原文上，否则退回整体解析。
    if body and (offset < 0 or anchor[offset : offset + len(body)] != body):
        body, offset = anchor, 0
    requirements, tree = build_requirements(body)
    if offset > 0:
        # 叶节点的 span 与 requirement 共用同一对象，只遍历树即可避免重复偏移。
        _shift_spans(tree, offset)
    if any(item.get("open_enumeration_mode") == "uncertain" for item in requirements):
        review_required = True
    return Candidate(
        parameter_name=name,
        original_number=number,
        original_text=anchor,
        normalized_text=clean,
        source_locator=locator,
        section=section,
        extraction_method=method,
        extraction_confidence=confidence,
        review_required=review_required,
        atomic_requirements=requirements,
        logic_tree=tree,
        extra_locators=extra_locators or [],
    )


def _table_candidate(region: Region):
    cells = region.cells or []
    roles = region.roles or []
    technical = _table_cell(cells, roles, "technical")
    if not technical:
        technical = "；".join(
            (value or "").strip()
            for value, role in zip(cells, roles, strict=False)
            if role == "unknown" and (value or "").strip()
        )
    name = _table_cell(cells, roles, "name")
    sequence = _table_cell(cells, roles, "seq")
    technical, removed = _strip_proof(technical)
    if not technical:
        return None, name, sequence, removed
    prefix = f"{sequence} {name}".strip()
    original = "\n".join(value for value in [prefix, technical] if value)
    locator = {**region.locator, "kind": "table", "section": region.section}
    candidate = _candidate_from_text(
        original,
        locator,
        section=region.section,
        number=sequence or None,
        confidence="high",
        review_required=_proof_needs_review(technical, removed),
        requirement_body=technical,
        requirement_offset=len(prefix) + 1 if prefix else 0,
    )
    candidate.parameter_name = _table_name(cells, roles, technical)
    if name:
        candidate.extra_locators = [{"name": name, "locator": region.locator}]
    return candidate, name, sequence, removed


def _filtered(region, code, reason):
    return CoverageRecord(
        disposition="filtered",
        region_kind=region.kind,
        original_text=region.text,
        source_locator=region.locator,
        reason_code=code,
        reason=reason,
    )


def _gap(region):
    status = (region.flags or {}).get("parse_gap") or "parse_gap"
    return CoverageRecord(
        disposition="parse_gap",
        region_kind="page_gap",
        original_text="",
        source_locator=region.locator,
        reason_code=status,
        reason="该页无法可靠解析，未虚构缺失内容",
    )


def extract(parsed: ParsedDocument, *, max_parameters: int = 1000) -> ExtractionResult:
    candidates = []
    coverage = []
    current = None
    current_lines = []
    current_locators = []
    current_section = ""

    def flush():
        nonlocal current, current_lines, current_locators
        if current is None:
            return
        text = "\n".join(current_lines).strip()
        if not text:
            current = None
            current_lines = []
            current_locators = []
            return
        candidate = _candidate_from_text(
            text,
            current["locator"],
            section=current_section,
            number=current["number"],
            method=current["method"],
            confidence=current["confidence"],
            review_required=current["review_required"],
            extra_locators=current_locators[1:],
        )
        candidate.parameter_name = current["name"] or candidate.parameter_name
        if len(candidates) < max_parameters:
            candidates.append(candidate)
            coverage.append(
                CoverageRecord(
                    "extracted",
                    "clause",
                    text,
                    current["locator"],
                    confidence=current["confidence"],
                    parameter=candidate,
                )
            )
        else:
            coverage.append(
                CoverageRecord(
                    "ambiguous",
                    "clause",
                    text,
                    current["locator"],
                    "PARAMETER_LIMIT",
                    "参数数量超过任务预算，需拆分上传",
                    "low",
                )
            )
        current = None
        current_lines = []
        current_locators = []

    for region in parsed.regions:
        if region.kind == "gap":
            flush()
            coverage.append(_gap(region))
            continue
        if region.kind == "heading":
            flush()
            current_section = region.section or region.text
            coverage.append(_filtered(region, "section_heading", "章节标题不作为独立技术参数"))
            continue
        if region.kind == "table_title":
            flush()
            coverage.append(_filtered(region, "table_title", "表格标题不作为技术参数"))
            continue
        if region.kind == "table_header":
            flush()
            coverage.append(_filtered(region, "table_header", "表格表头不作为技术参数"))
            continue
        if region.kind == "table_row":
            flush()
            if (region.flags or {}).get("repeated_header"):
                coverage.append(_filtered(region, "repeated_header", "重复表头"))
                continue
            if _section_noise(region.section):
                coverage.append(
                    _filtered(
                        region,
                        "non_technical_section",
                        f"章节不属于技术参数范围：{region.section[:80]}",
                    )
                )
                continue
            if COMPETITOR.search(region.text):
                coverage.append(
                    _filtered(region, "competitor_content", "表格行属于友商或竞品对比内容")
                )
                continue
            proof_cells = [
                (value or "").strip()
                for value, role in zip(region.cells or [], region.roles or [], strict=False)
                if role in {"proof", "commercial", "remark"} and (value or "").strip()
            ]
            for value in proof_cells:
                coverage.append(
                    CoverageRecord(
                        "filtered",
                        f"{region.kind}_column",
                        value,
                        {**region.locator, "filtered_column": True},
                        "proof_or_commercial_column",
                        "证明材料/商务/备注列不进入技术条件",
                    )
                )
            candidate, name, sequence, removed = _table_candidate(region)
            for fragment in removed:
                coverage.append(
                    CoverageRecord(
                        "filtered",
                        f"{region.kind}_proof_clause",
                        fragment["text"],
                        {
                            **region.locator,
                            "filtered_column": True,
                            "filtered_span": {
                                "start": fragment["start"],
                                "end": fragment["end"],
                            },
                        },
                        "proof_material",
                        "已将证明材料子句从技术参数中分离，原文位置保留",
                        "medium",
                    )
                )
            if candidate is None:
                code = "proof_material" if removed else "non_technical_table_row"
                reason = (
                    "表格技术单元格只有证明材料，未生成技术参数"
                    if removed
                    else "表格行没有技术参数正文"
                )
                coverage.append(_filtered(region, code, reason))
                continue
            if not sequence and not name and candidates:
                previous = candidates[-1]
                _merge_continuation(previous, candidate)
                previous.extra_locators.append({"locator": region.locator, "continued": True})
                coverage.append(
                    CoverageRecord(
                        "extracted",
                        "table_continuation",
                        region.text,
                        region.locator,
                        "CONTINUATION",
                        "按表格结构并入上一条参数",
                        "high",
                        parameter=previous,
                    )
                )
                continue
            if len(candidates) >= max_parameters:
                coverage.append(
                    CoverageRecord(
                        "ambiguous",
                        region.kind,
                        region.text,
                        region.locator,
                        "PARAMETER_LIMIT",
                        "参数数量超过任务预算，需拆分上传",
                        "low",
                    )
                )
                continue
            candidates.append(candidate)
            coverage.append(
                CoverageRecord(
                    "extracted",
                    region.kind,
                    region.text,
                    region.locator,
                    confidence="high",
                    parameter=candidate,
                )
            )
            continue
        text = region.text.strip()
        if not text:
            coverage.append(_filtered(region, "empty_region", "空白区域"))
            continue
        cleaned, removed = _strip_proof(text)
        mixed_review = _proof_needs_review(cleaned, removed)
        if removed:
            for fragment in removed:
                coverage.append(
                    CoverageRecord(
                        "filtered",
                        f"{region.kind}_proof_clause",
                        fragment["text"],
                        {
                            **region.locator,
                            "filtered_span": {
                                "start": fragment["start"],
                                "end": fragment["end"],
                            },
                        },
                        "proof_material",
                        "已将证明材料子句从技术条件中分离，原文位置保留",
                        "medium",
                    )
                )
            if not cleaned or not _TECH_ACTION.search(cleaned):
                # 纯证明材料行不生成技术参数；不清空上一条参数的内容。
                continue
            text = cleaned
        noise = _noise_reason(text, region.section or current_section)
        if noise:
            flush()
            coverage.append(_filtered(region, noise[0], noise[1]))
            continue
        number_match = TOP_NUMBER.match(text)
        bare_match = BARE_NUMBER.match(text) if not number_match else None
        bullet_match = BULLET.match(text)
        if number_match or bare_match:
            match = number_match or bare_match
            number = match.group(1)
            body = match.group(2).strip()
            flush()
            current_section = region.section or current_section
            current = {
                "number": number,
                "name": _name_from_text(body, number),
                "locator": region.locator,
                "method": "structural_numbered",
                "confidence": "high",
                "review_required": mixed_review,
            }
            current_lines = [text]
            current_locators = [region.locator]
            continue
        if bullet_match:
            flush()
            current_section = region.section or current_section
            current = {
                "number": None,
                "name": _name_from_text(bullet_match.group(1)),
                "locator": region.locator,
                "method": "structural_bullet",
                "confidence": "medium",
                "review_required": mixed_review,
            }
            current_lines = [text]
            current_locators = [region.locator]
            continue
        if current is not None:
            if SUBCLAUSE.search(text) or re.match(r"^\s*(?:其中|包括|并|且|同时|此外|以及)", text):
                current_lines.append(text)
                current_locators.append(region.locator)
                current["review_required"] = current["review_required"] or mixed_review
                continue
            if current["method"] == "structural_numbered" or not re.search(r"[。；;！？!?]$", text):
                current_lines.append(text)
                current_locators.append(region.locator)
                current["review_required"] = current["review_required"] or mixed_review
                continue
        flush()
        current_section = region.section or current_section
        if not TECH_SIGNAL.search(text):
            coverage.append(
                CoverageRecord(
                    "ambiguous",
                    region.kind,
                    text,
                    region.locator,
                    "UNCERTAIN_PARAMETER",
                    "无法确认是否为独立技术参数，保留原文等待人工确认",
                    "low",
                )
            )
            continue
        current = {
            "number": None,
            "name": _name_from_text(text),
            "locator": region.locator,
            "method": "structural_paragraph",
            "confidence": "medium",
            "review_required": True,
        }
        current_lines = [text]
        current_locators = [region.locator]
    flush()
    skipped = sum(1 for item in coverage if item.disposition == "filtered")
    ambiguous = sum(1 for item in coverage if item.disposition == "ambiguous")
    gaps = [item for item in coverage if item.disposition == "parse_gap"]
    complete = bool(candidates) and not gaps and ambiguous == 0
    filtered_reasons = Counter(
        item.reason_code or "UNKNOWN" for item in coverage if item.disposition == "filtered"
    )
    ambiguous_reasons = Counter(
        item.reason_code or "UNKNOWN" for item in coverage if item.disposition == "ambiguous"
    )
    failure_code = None
    failure_reason = None
    if not candidates:
        failure_code = "NO_TECHNICAL_PARAMETERS"
        filtered_detail = (
            "、".join(f"{code}={count}" for code, count in sorted(filtered_reasons.items())) or "无"
        )
        ambiguous_detail = (
            "、".join(f"{code}={count}" for code, count in sorted(ambiguous_reasons.items()))
            or "无"
        )
        failure_reason = (
            f"未生成有效顶层技术参数：过滤 {skipped} 个区域（{filtered_detail}），"
            f"歧义 {ambiguous} 个区域（{ambiguous_detail}），解析缺口 {len(gaps)} 处"
        )
    details = {
        "source_type": parsed.source_type,
        "details": parsed.details,
        "regions": len(parsed.regions),
        "extracted": len(candidates),
        "filtered": skipped,
        "ambiguous": ambiguous,
        "parse_gaps": len(gaps),
        "coverage_status": "complete" if complete else "incomplete",
        "filtered_reason_counts": dict(sorted(filtered_reasons.items())),
        "ambiguous_reason_counts": dict(sorted(ambiguous_reasons.items())),
        "failure_code": failure_code,
        "failure_reason": failure_reason,
    }
    return ExtractionResult(
        candidates=candidates,
        coverage=coverage,
        details=details,
        gaps=parsed.gaps,
        skipped_count=skipped,
        ambiguous_count=ambiguous,
        coverage_status="complete" if complete else "incomplete",
        failure_code=failure_code,
        failure_reason=failure_reason,
    )
