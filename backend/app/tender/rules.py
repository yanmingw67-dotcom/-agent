"""P2.1 招标参数边界、噪声过滤与原子子要求。

本模块只做结构化解释，不判断产品是否支持。顶层参数数量由结构决定：
表格数据行/编号条款是顶层；内部 (1)(2)(3) 或协议枚举只生成子要求。
"""

import re
import uuid
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
    r"(不低于|不少于|不小于|不高于|不超过|不大于|至少|至多|>=|<=|>|<)\s*"
    r"([0-9]+(?:\.[0-9]+)?|[_＿]{2,})?\s*"
    r"(Gbps|Mbps|QPS|TPS|ms|秒|分钟|小时|个|种|路|万)?",
    re.I,
)


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
    if PROOF.search(value) and not PROOF_EXPORT.search(value):
        return "proof_material", "证明材料、截图、检测报告或证书要求"
    if COMMERCIAL.search(value):
        return "commercial_terms", "售后服务、质保、培训、报价或付款条款"
    if COMPETITOR.search(value):
        return "competitor_content", "友商或竞品对比内容"
    return None


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
        return None, None, None
    operator, value, unit = match.groups()
    if not value or set(value) <= {"_", "＿"}:
        return None, unit, "blank_threshold"
    return value, unit, None


def _ordered_atoms(text: str, base: int = 0):
    matches = list(SUBCLAUSE.finditer(text))
    if len(matches) < 2:
        return []
    atoms = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        atom = text[match.end() : end].strip(" \n；;，,")
        if atom:
            atoms.append((atom, base + match.end(), base + end))
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


def _atomic(text: str, base: int = 0):
    subclauses = _ordered_atoms(text, base)
    if subclauses:
        atoms = subclauses
        relation = "AND"
        requirement_type = "explicit_subclause"
    else:
        or_parts = re.split(r"\s*(?:或|或者)\s*", text)
        if len(or_parts) >= 2 and all(len(part.strip(" ，,。；;")) >= 2 for part in or_parts):
            atoms = []
            cursor = 0
            for part in or_parts:
                value = part.strip(" ，,。；;")
                start = text.find(value, cursor)
                if start < 0:
                    start = cursor
                atoms.append((value, base + start, base + start + len(value)))
                cursor = start + len(value)
            relation = "OR"
            requirement_type = "alternative"
        else:
            atoms = _enumerated_atoms(text, base)
            relation = "AND"
            requirement_type = "capability" if not atoms else "enumerated_condition"
    minimum = re.search(
        r"(?:至少|不少于|不小于)\s*(?:支持|具备|提供)?\s*"
        r"([一二三四五六七八九十\d]+)\s*(?:种|项|个|路)",
        text,
    )
    if minimum:
        atoms = [(text.strip(" 。；;"), base, base + len(text))]
        relation = "AT_LEAST"
        requirement_type = "minimum_count"
    if not atoms:
        atoms = [(text.strip(" 。；;"), base, base + len(text))]
        requirement_type = "capability"
    group_id = uuid.uuid4().hex
    requirements = []
    for value, start, end in atoms:
        value = value.strip(" \n；;，,")
        if not value:
            continue
        value_threshold, unit, ambiguity = _threshold(value)
        conditions = _conditions(value)
        mandatory = not bool(re.search(r"可选|允许|可以", value)) or bool(
            re.search(r"必须|须|要求", value)
        )
        requirements.append(
            {
                "requirement_id": uuid.uuid4().hex,
                "original_text": value,
                "requirement_type": requirement_type if not minimum else "minimum_count",
                "logical_relation": relation,
                "logical_group_id": group_id,
                "is_mandatory": mandatory,
                "threshold": value_threshold,
                "unit": unit,
                "conditions": conditions,
                "ambiguity": ambiguity,
                "source_span": {"start": start, "end": end},
                "parent_requirement_id": None,
                "depth": 0,
            }
        )
    if minimum and requirements:
        requirements[0]["threshold"] = minimum.group(1)
        requirements[0]["requirement_type"] = "minimum_count"
    return requirements


def build_requirements(text: str):
    requirements = _atomic(text)
    group = requirements[0]["logical_group_id"] if requirements else None
    tree = {
        "relation": requirements[0]["logical_relation"] if requirements else "AND",
        "logical_group_id": group,
        "children": [
            {"requirement_id": item["requirement_id"], "relation": "LEAF"} for item in requirements
        ],
    }
    return requirements, tree


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
):
    clean = normalize(text)
    name = _name_from_text(text, number)
    body = _strip_number(text)[1] if number else text
    requirements, tree = build_requirements(body)
    return Candidate(
        parameter_name=name,
        original_number=number,
        original_text=text.strip(),
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
    if not technical:
        return None, name, sequence
    original = "\n".join(value for value in [f"{sequence} {name}".strip(), technical] if value)
    locator = {**region.locator, "kind": "table", "section": region.section}
    candidate = _candidate_from_text(
        technical,
        locator,
        section=region.section,
        number=sequence or None,
        confidence="high",
    )
    candidate.parameter_name = _table_name(cells, roles, technical)
    candidate.original_text = original
    candidate.normalized_text = normalize(original)
    if name:
        candidate.extra_locators = [{"name": name, "locator": region.locator}]
    return candidate, name, sequence


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
            candidate, name, sequence = _table_candidate(region)
            if candidate is None:
                coverage.append(
                    _filtered(region, "non_technical_table_row", "表格行没有技术参数正文")
                )
                continue
            if not sequence and not name and candidates:
                previous = candidates[-1]
                previous.original_text = previous.original_text + "\n" + candidate.original_text
                previous.normalized_text = normalize(previous.original_text)
                previous.atomic_requirements, previous.logic_tree = build_requirements(
                    previous.original_text
                )
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
                "review_required": False,
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
                "review_required": False,
            }
            current_lines = [text]
            current_locators = [region.locator]
            continue
        if current is not None:
            if SUBCLAUSE.search(text) or re.match(r"^\s*(?:其中|包括|并|且|同时|此外|以及)", text):
                current_lines.append(text)
                current_locators.append(region.locator)
                continue
            if current["method"] == "structural_numbered" or not re.search(r"[。；;！？!?]$", text):
                current_lines.append(text)
                current_locators.append(region.locator)
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
    complete = not gaps and ambiguous == 0
    details = {
        "source_type": parsed.source_type,
        "details": parsed.details,
        "regions": len(parsed.regions),
        "extracted": len(candidates),
        "filtered": skipped,
        "ambiguous": ambiguous,
        "parse_gaps": len(gaps),
        "coverage_status": "complete" if complete else "incomplete",
    }
    return ExtractionResult(
        candidates=candidates,
        coverage=coverage,
        details=details,
        gaps=parsed.gaps,
        skipped_count=skipped,
        ambiguous_count=ambiguous,
        coverage_status="complete" if complete else "incomplete",
    )
