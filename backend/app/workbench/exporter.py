"""P2.3 导出引擎：从已持久化的 P2.2 结果生成可编辑 Word / Excel。

安全边界：
- 只读取 evaluation_jobs 固定的 publication_id 与已持久化评估行，不重新判定；
- 发布清单哈希或证据上下文不可用时失败关闭，绝不回退当前生效版本；
- Word 使用真实表格；Excel 单元格做公式注入转义并限制长度；
- 文件只在成功生成后登记可下载，下载仍需身份、任务归属与令牌校验。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.evaluation_models import EvaluationJob, ParameterEvaluation
from app.db.models import AuditEvent, Category
from app.db.session import session_factory
from app.db.workbench_models import EvaluationExportJob
from app.evaluation.service import _parameter_payload, job_json
from app.workbench.context import (
    enrich_evidence,
    pinned_capabilities,
    source_type_label,
    verify_export_evidence,
)
from app.workbench.reviews import latest_reviews, review_json, review_summary

REVIEWABLE = {"succeeded", "partial"}
VERDICTS = {"支持", "不支持", "无法确认"}
ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
FORMULA_PREFIXES = ("=", "+", "-", "@")


def _text(value, limit: int):
    text = str(value if value is not None else "")
    text = ILLEGAL_XML.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def _cell_text(value, limit: int):
    """Excel 单元格：先截断，再对可触发公式的前缀加单引号。"""
    text = _text(value, limit)
    # 部分表格软件会忽略前导空白后再解释公式，因此按去除空白后的首字符判断。
    if text.lstrip(" \t\r\n").startswith(FORMULA_PREFIXES):
        text = "'" + text
    return text


def _dt(value):
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _join(values):
    if isinstance(values, list):
        return "；".join(str(item) for item in values if item)
    return str(values or "")


def _evidence_label(item):
    return (
        f"{source_type_label(item.get('kind'))} · "
        f"{item.get('source_file') or '来源文件未知'} · "
        f"{item.get('locator_text') or '位置未知'}"
    )


def report_data(db, export: EvaluationExportJob):
    settings = get_settings()
    job = db.get(EvaluationJob, export.job_id)
    if not job or job.principal_id != export.principal_id:
        raise AppError(404, "EVALUATION_JOB_NOT_FOUND", "评估任务不存在")
    if job.status not in REVIEWABLE:
        raise AppError(
            409,
            "EXPORT_JOB_NOT_READY",
            "只有评估成功或部分成功的任务才能导出",
        )
    capabilities, context_error = pinned_capabilities(db, job)
    if context_error:
        raise AppError(
            409,
            "EXPORT_EVIDENCE_CONTEXT_UNAVAILABLE",
            "任务固定发布版本校验失败，不能导出；系统不会替换为当前生效版本",
        )
    # 必须在生成文件前完成证据再验证；PinnedKnowledge 的缓存保证每条证据只回读一次。
    verify_export_evidence(db, job, capabilities)
    rows = list(db.scalars(select(ParameterEvaluation).where(ParameterEvaluation.job_id == job.id)))
    if not rows or len(rows) != job.parameter_count:
        raise AppError(
            409,
            "EXPORT_RESULTS_INCOMPLETE",
            "评估结果不完整，不能生成与页面一致的导出文件",
        )
    if len(rows) > settings.export_max_parameters:
        raise AppError(422, "EXPORT_PARAMETER_LIMIT", "参数数量超过导出上限")
    order = {item["parameter_id"]: index for index, item in enumerate(job.parameter_snapshot or [])}
    rows.sort(key=lambda item: order.get(item.parameter_id, 999999))
    requirements = {}
    for parameter in job.parameter_snapshot or []:
        for item in parameter.get("requirements") or []:
            requirements[item.get("requirement_id")] = item
    reviews = latest_reviews(db, job.id)
    parameters = []
    evidence_index = {}
    counts = {"支持": 0, "不支持": 0, "无法确认": 0}
    failed_count = 0
    atomic_total = 0
    for position, row in enumerate(rows, 1):
        payload = _parameter_payload(db, row)
        review = reviews.get(row.parameter_id)
        if review and review.auto_verdict != row.verdict:
            raise AppError(
                409,
                "EXPORT_REVIEW_INTEGRITY_ERROR",
                "人工复核记录与系统自动结论不一致，已停止导出",
            )
        atoms = []
        for atom in payload.get("atomic_requirements") or []:
            requirement = requirements.get(atom.get("requirement_id")) or {}
            enriched_atom = dict(atom)
            enriched_atom.update(
                {
                    "logical_relation": requirement.get("logical_relation") or "AND",
                    "mandatory_status": requirement.get("mandatory_status")
                    or ("required" if atom.get("necessary") else "optional"),
                    "threshold": requirement.get("threshold"),
                    "threshold_operator": requirement.get("threshold_operator"),
                    "unit": requirement.get("unit"),
                    "conditions": requirement.get("conditions") or [],
                    "conditions_text": _join(requirement.get("conditions")),
                    "ambiguity": requirement.get("ambiguity"),
                }
            )
            atom_evidence = []
            for raw in atom.get("evidence") or []:
                enriched = enrich_evidence(raw, capabilities, None)
                if enriched.get("context_status") != "available":
                    raise AppError(
                        409,
                        "EXPORT_EVIDENCE_UNAVAILABLE",
                        "固定发布版本中缺少证据对应的已审核能力修订，不能导出",
                    )
                key = enriched.get("evidence_id")
                atom_evidence.append(enriched)
                label = f"参数 {position}·子要求 {atom.get('order_index')}（{atom.get('verdict')}）"
                if key not in evidence_index:
                    evidence_index[key] = {
                        **enriched,
                        "used_for": [label],
                        "parameter_orders": [position],
                    }
                else:
                    evidence_index[key]["used_for"].append(label)
                    if position not in evidence_index[key]["parameter_orders"]:
                        evidence_index[key]["parameter_orders"].append(position)
            enriched_atom["evidence"] = atom_evidence
            enriched_atom["evidence_located"] = (
                "无证据"
                if not atom_evidence
                else (
                    "可定位"
                    if all(item.get("context_status") == "available" for item in atom_evidence)
                    else "上下文不可用"
                )
            )
            atoms.append(enriched_atom)
        atomic_total += len(atoms)
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
        if row.error_code:
            failed_count += 1
        parameter_evidence = []
        seen = set()
        for atom in atoms:
            for item in atom["evidence"]:
                if item.get("evidence_id") not in seen:
                    seen.add(item.get("evidence_id"))
                    parameter_evidence.append(item)
        review_payload = review_json(review) if review else None
        parameters.append(
            {
                "order_index": position,
                "parameter_id": row.parameter_id,
                "parameter_name": row.parameter_name,
                "original_text": row.original_text,
                "verdict": row.verdict,
                "rationale": row.rationale,
                "logic_reason": (row.logic_json or {}).get("reason")
                or (row.details_json or {}).get("aggregate_reason")
                or "",
                "error_code": row.error_code,
                "failed": bool(row.error_code),
                "atoms": atoms,
                "evidence": parameter_evidence,
                "review": review_payload,
                "review_status": review.review_status if review else "pending",
                "review_status_label": (
                    review_json(review)["review_status_label"] if review else "待复核"
                ),
                "human_final_verdict": review.final_verdict if review else None,
                "human_comment": review.comment if review else "",
                "human_adjust_reason": review.adjust_reason if review else "",
                "requires_reevaluation": review.requires_reevaluation if review else False,
            }
        )
    if len(evidence_index) > settings.export_max_evidence:
        raise AppError(422, "EXPORT_EVIDENCE_LIMIT", "证据数量超过导出上限")

    issues = []
    for parameter in parameters:
        if parameter["failed"]:
            issues.append(
                {
                    "order_index": parameter["order_index"],
                    "parameter_name": parameter["parameter_name"],
                    "issue_type": "评估失败",
                    "detail": f"评估执行失败（{parameter['error_code']}），不能视为支持。",
                    "atom_refs": "",
                    "suggestion": "检查评估任务与固定知识版本后重新评估；重新评估前不得改为支持。",
                    "review_status_label": parameter["review_status_label"],
                    "human_final_verdict": parameter["human_final_verdict"] or "",
                }
            )
            continue
        if parameter["verdict"] == "无法确认":
            unknown = [atom for atom in parameter["atoms"] if atom["verdict"] == "无法确认"]
            lacking = [atom for atom in unknown if not atom.get("evidence")]
            issue_type = "缺少可核验证据" if lacking else "证据不足以确认"
            detail_parts = [
                f"子要求 {atom['order_index']}：{atom.get('reason') or '无法确认'}"
                for atom in unknown
            ] or [parameter["rationale"]]
            issues.append(
                {
                    "order_index": parameter["order_index"],
                    "parameter_name": parameter["parameter_name"],
                    "issue_type": issue_type,
                    "detail": "；".join(detail_parts),
                    "atom_refs": "、".join(str(atom["order_index"]) for atom in unknown),
                    "suggestion": "核对招标原文、产品能力适用范围与限制条件；补充已审核且可核验的知识或重新评估。",
                    "review_status_label": parameter["review_status_label"],
                    "human_final_verdict": parameter["human_final_verdict"] or "",
                }
            )
        unavailable = [
            item for item in parameter["evidence"] if item.get("context_status") != "available"
        ]
        if unavailable:
            issues.append(
                {
                    "order_index": parameter["order_index"],
                    "parameter_name": parameter["parameter_name"],
                    "issue_type": "证据上下文不可用",
                    "detail": "、".join(item.get("evidence_id", "") for item in unavailable),
                    "atom_refs": "",
                    "suggestion": "回到固定发布版本核对能力修订；不得替换为当前生效版本或其他能力。",
                    "review_status_label": parameter["review_status_label"],
                    "human_final_verdict": parameter["human_final_verdict"] or "",
                }
            )
        if parameter["review"]:
            if parameter["review_status"] == "reassessment_required":
                issues.append(
                    {
                        "order_index": parameter["order_index"],
                        "parameter_name": parameter["parameter_name"],
                        "issue_type": "人工要求重新评估",
                        "detail": parameter["human_comment"] or "人工复核要求重新评估。",
                        "atom_refs": "",
                        "suggestion": "按人工意见重新评估；系统自动结论与证据保持原样可追溯。",
                        "review_status_label": parameter["review_status_label"],
                        "human_final_verdict": "",
                    }
                )
            elif (
                parameter["human_final_verdict"]
                and parameter["human_final_verdict"] != parameter["verdict"]
            ):
                issues.append(
                    {
                        "order_index": parameter["order_index"],
                        "parameter_name": parameter["parameter_name"],
                        "issue_type": "人工复核有不同意见",
                        "detail": (
                            f"系统自动结论：{parameter['verdict']}；"
                            f"人工应答：{parameter['human_final_verdict']}；"
                            f"理由：{parameter['human_adjust_reason'] or parameter['human_comment']}"
                        ),
                        "atom_refs": "",
                        "suggestion": "按人工意见处理，但导出与页面继续分列展示自动结论与人工应答。",
                        "review_status_label": parameter["review_status_label"],
                        "human_final_verdict": parameter["human_final_verdict"],
                    }
                )
    human_reviews = [parameter for parameter in parameters if parameter["review"] is not None]
    summary = review_summary(db, job)
    digest_payload = {
        "job_id": job.id,
        "publication_id": job.publication_id,
        "publication_hash": job.publication_hash,
        "parameters": [
            {
                "parameter_id": parameter["parameter_id"],
                "verdict": parameter["verdict"],
                "review_id": (parameter["review"] or {}).get("review_id"),
            }
            for parameter in parameters
        ],
        "evidence_ids": sorted(key for key in evidence_index if key),
    }
    data_digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    data = {
        "job": job_json(job),
        "job_id": job.id,
        "category_name": db.scalar(select(Category.name).where(Category.id == job.category_id))
        or "",
        "publication_id": job.publication_id,
        "publication_hash": job.publication_hash,
        "published_snapshot_id": job.published_snapshot_id,
        "parameter_count": len(parameters),
        "atomic_count": atomic_total,
        "counts": counts,
        "failed_count": failed_count,
        "parameters": parameters,
        "evidence_index": sorted(
            evidence_index.values(),
            key=lambda item: (item.get("parameter_orders") or [999999])[0],
        ),
        "evidence_count": len(evidence_index),
        "human_reviews": human_reviews,
        "review_count": len(human_reviews),
        "review_summary": summary,
        "issues": issues,
        "data_digest": data_digest,
        "generated_at": datetime.now(UTC),
    }
    return data


def _set_doc_font(run, *, size=9, bold=False, color=None):
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    rpr = run._element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:eastAsia"), "微软雅黑")


def _shade(cell, fill):
    cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill}"/>'))


def _write_cell(cell, value, *, bold=False, size=8.5, align=None, color=None):
    cell.text = ""
    paragraph = cell.paragraphs[0]
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(_text(value, 20000))
    _set_doc_font(run, size=size, bold=bold, color=color)
    paragraph.paragraph_format.space_after = Pt(0)


def _header_row(table, labels):
    for index, label in enumerate(labels):
        cell = table.rows[0].cells[index]
        _write_cell(cell, label, bold=True, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
        _shade(cell, "D9E2F3")
    table.alignment = WD_TABLE_ALIGNMENT.CENTER


def _add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    _header_row(table, headers)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            _write_cell(cells[index], value)
    if widths:
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    return table


def write_docx(data, path: Path):
    settings = get_settings()
    limit = settings.export_max_cell_chars
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)
    section.left_margin = Cm(1.4)
    section.right_margin = Cm(1.4)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("产品技术参数符合性评估报告")
    _set_doc_font(run, size=20, bold=True, color=(0x1F, 0x3A, 0x5F))
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_doc_font(
        subtitle.add_run("系统自动评估结论与人工复核意见分列呈现"),
        size=10,
        color=(0x55, 0x5F, 0x6D),
    )

    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Table Grid"
    meta_rows = [
        ("产品品类", data["category_name"] or "未命名品类"),
        ("评估任务编号", data["job_id"]),
        ("评估状态", data["job"]["status"]),
        ("创建时间", _dt(data["job"].get("created_at"))),
        ("完成时间", _dt(data["job"].get("finished_at"))),
        (
            "固定知识发布版本",
            f"{data['publication_id']}（哈希 {data['publication_hash'][:16]}…）",
        ),
        (
            "评估规模",
            f"参数 {data['parameter_count']} 条 / 原子子要求 {data['atomic_count']} 项 / 证据 {data['evidence_count']} 条",
        ),
        (
            "结论统计",
            f"支持 {data['counts']['支持']} 条；不支持 {data['counts']['不支持']} 条；"
            f"无法确认 {data['counts']['无法确认']} 条；执行失败 {data['failed_count']} 条",
        ),
        (
            "人工复核",
            f"已复核 {data['review_summary']['reviewed']} 条；待复核 {data['review_summary']['pending']} 条",
        ),
    ]
    for label, value in meta_rows:
        cells = meta.add_row().cells
        _write_cell(cells[0], label, bold=True, size=9)
        _shade(cells[0], "F2F5F9")
        _write_cell(cells[1], value, size=9)
        cells[0].width = Cm(6)
        cells[1].width = Cm(20)

    doc.add_paragraph()
    notes = [
        "1. 本报告由评估任务已持久化的自动评估结果生成，不重新调用模型判定，不修改系统自动结论。",
        "2. 人工复核记录单独列示，只记录复核意见与人工应答，不改变系统自动结论与证据。",
        "3. 报告只使用任务固定发布版本中已审核的产品能力证据；证据不足时保留“无法确认”，不得改为“支持”。",
        "4. 报告不包含招标文件中的证明材料要求、管理员未审核草稿、其他用户任务数据或产品源码全文。",
        f"5. 导出数据摘要（SHA-256）：{data['data_digest']}",
    ]
    for note in notes:
        paragraph = doc.add_paragraph()
        _set_doc_font(paragraph.add_run(note), size=9, color=(0x44, 0x4B, 0x55))
        paragraph.paragraph_format.space_after = Pt(2)

    doc.add_heading("一、逐条参数结论", level=1)
    rows = []
    for item in data["parameters"]:
        name_and_text = f"{item['parameter_name']}\n{item['original_text']}"
        if item["failed"]:
            name_and_text += f"\n【评估执行失败：{item['error_code']}】"
        rationale = item["rationale"]
        if item["logic_reason"] and item["logic_reason"] not in rationale:
            rationale = f"{rationale}\n逻辑汇总：{item['logic_reason']}"
        verdict = item["verdict"]
        if item["failed"]:
            verdict = "无法确认（执行失败关闭）"
        rows.append(
            [
                item["order_index"],
                _text(name_and_text, limit),
                verdict,
                _text(rationale, limit),
                item["review_status_label"],
            ]
        )
    _add_table(
        doc,
        ["序号", "招标技术参数（名称与原文）", "符合性结论", "技术论证说明", "复核状态"],
        rows,
        widths=[1.2, 7.4, 2.8, 12.4, 3.2],
    )
    doc.add_paragraph()

    doc.add_heading("二、无法确认与执行失败清单", level=1)
    unknown = [
        item for item in data["parameters"] if item["verdict"] == "无法确认" or item["failed"]
    ]
    if not unknown:
        doc.add_paragraph("本次评估没有无法确认或执行失败的参数。")
    else:
        for item in unknown:
            paragraph = doc.add_paragraph(style="List Number")
            _set_doc_font(
                paragraph.add_run(f"{item['parameter_name']}（序号 {item['order_index']}）"),
                size=10,
                bold=True,
            )
            reason = item["rationale"]
            if item["failed"]:
                reason = f"评估执行失败（{item['error_code']}），按无法确认关闭，不能视为支持。"
            _set_doc_font(paragraph.add_run(f"\n原因：{_text(reason, limit)}"), size=9)
            unknown_atoms = [atom for atom in item["atoms"] if atom["verdict"] == "无法确认"]
            if unknown_atoms:
                _set_doc_font(
                    paragraph.add_run(
                        "\n待核实子要求："
                        + "；".join(
                            f"子要求 {atom['order_index']}：{_text(atom.get('reason'), limit)}"
                            for atom in unknown_atoms
                        )
                    ),
                    size=9,
                )
            _set_doc_font(
                paragraph.add_run(
                    "\n建议：核对招标原文、产品能力适用范围与限制条件，补充已审核且可核验的知识或重新评估。"
                ),
                size=9,
                color=(0x7A, 0x4B, 0x00),
            )

    doc.add_heading("三、人工复核与人工应答（不改变系统自动结论）", level=1)
    if not data["human_reviews"]:
        doc.add_paragraph("本次导出时没有人工复核记录。")
    else:
        doc.add_paragraph(
            "下表左列的系统自动结论来自 P2.2 自动评估；人工应答与理由不会覆盖自动结论，回查历史时仍以自动结论和原证据为准。"
        )
        review_rows = []
        for item in data["human_reviews"]:
            review = item["review"]
            review_rows.append(
                [
                    item["order_index"],
                    item["parameter_name"],
                    item["verdict"],
                    item["human_final_verdict"] or "未填写",
                    item["review_status_label"],
                    _text(
                        f"复核人：{review.get('reviewer_id', '')}\n"
                        f"时间：{_dt(review.get('created_at'))}\n"
                        f"意见：{review.get('comment') or '无'}\n"
                        f"调整理由：{review.get('adjust_reason') or '无'}",
                        limit,
                    ),
                ]
            )
        _add_table(
            doc,
            ["序号", "参数名称", "系统自动结论", "人工应答结论", "复核状态", "复核意见与理由"],
            review_rows,
            widths=[1.2, 5.4, 3.2, 3.2, 3.8, 10.2],
        )
    doc.add_paragraph()

    doc.add_heading("四、证据附录", level=1)
    if not data["evidence_index"]:
        doc.add_paragraph("本次评估没有已核验证据。无法确认的参数不得改判为支持。")
    else:
        doc.add_paragraph(
            "证据引文为已核验原文的节选，仅用于说明论证；不以证明材料要求代替产品能力，也不附产品源码全文。"
        )
        evidence_rows = []
        for item in data["evidence_index"]:
            evidence_rows.append(
                [
                    item.get("evidence_id", ""),
                    source_type_label(item.get("kind")),
                    _text(
                        f"{item.get('source_file') or '来源文件未知'}\n"
                        f"{item.get('locator_text') or '位置未知'}",
                        limit,
                    ),
                    _text(item.get("quote"), min(limit, 1000)),
                    item.get("verification_state", ""),
                    _text(
                        f"能力：{item.get('capability_name') or '未提供'}\n"
                        f"适用范围：{item.get('supported_scope_text') or '未提供'}\n"
                        f"限制条件：{item.get('limitations_text') or '未提供'}",
                        limit,
                    ),
                    _text("；".join(item.get("used_for") or []), limit),
                ]
            )
        _add_table(
            doc,
            [
                "证据ID",
                "类型",
                "来源文件与位置",
                "引文（节选）",
                "核验状态",
                "适用范围与限制",
                "关联参数",
            ],
            evidence_rows,
            widths=[2.6, 1.3, 5.2, 8.6, 2.4, 7.4, 3.0],
        )

    doc.add_heading("五、问题与待确认清单", level=1)
    if not data["issues"]:
        doc.add_paragraph("本次导出没有待确认问题。")
    else:
        issue_rows = [
            [
                item["order_index"],
                item["parameter_name"],
                item["issue_type"],
                _text(item["detail"], limit),
                _text(item["suggestion"], limit),
                item["review_status_label"],
            ]
            for item in data["issues"]
        ]
        _add_table(
            doc,
            ["序号", "参数名称", "问题类型", "具体说明", "建议核实事项", "复核状态"],
            issue_rows,
            widths=[1.2, 4.2, 3.2, 9.4, 8.0, 3.2],
        )

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_doc_font(
        footer.add_run(
            f"P2.3 评估工作台导出 · 任务 {data['job_id']} · 生成于 {_dt(data['generated_at'])}"
        ),
        size=8,
        color=(0x66, 0x6E, 0x7A),
    )
    doc.save(path)


def _style_sheet(ws, headers, widths, *, freeze="A2"):
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(name="Microsoft YaHei", bold=True, color="FFFFFF", size=10)
    border = Border(
        left=Side(style="thin", color="B7C4D1"),
        right=Side(style="thin", color="B7C4D1"),
        top=Side(style="thin", color="B7C4D1"),
        bottom=Side(style="thin", color="B7C4D1"),
    )
    for index, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=index, value=_cell_text(header, 200))
        cell.fill = fill
        cell.font = font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(index)].width = widths[index - 1]
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = freeze


def _append_rows(ws, rows, *, start=2):
    border = Border(
        left=Side(style="thin", color="D9E2F3"),
        right=Side(style="thin", color="D9E2F3"),
        top=Side(style="thin", color="D9E2F3"),
        bottom=Side(style="thin", color="D9E2F3"),
    )
    for row_index, values in enumerate(rows, start):
        for column_index, value in enumerate(values, 1):
            cell = ws.cell(row=row_index, column=column_index)
            if isinstance(value, int) and not isinstance(value, bool):
                cell.value = value
                cell.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
            else:
                cell.value = _cell_text(value, get_settings().export_max_cell_chars)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
            cell.font = Font(name="Microsoft YaHei", size=9)
        ws.row_dimensions[row_index].height = 42
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{max(1, ws.max_row)}"


def write_xlsx(data, path: Path):
    workbook = Workbook()
    summary = workbook.active
    summary.title = "评估结果汇总"
    headers = [
        "序号",
        "参数名称",
        "招标参数原文",
        "系统自动结论",
        "技术论证说明",
        "复核状态",
        "人工应答结论",
        "人工复核意见",
        "人工调整理由",
        "是否需重新评估",
        "参数执行状态",
    ]
    rows = []
    for item in data["parameters"]:
        rows.append(
            [
                item["order_index"],
                item["parameter_name"],
                item["original_text"],
                item["verdict"],
                item["rationale"],
                item["review_status_label"],
                item["human_final_verdict"] or "",
                item["human_comment"],
                item["human_adjust_reason"],
                "是" if item["requires_reevaluation"] else "否",
                f"失败：{item['error_code']}" if item["failed"] else "成功",
            ]
        )
    _style_sheet(
        summary,
        headers,
        [8, 24, 60, 14, 70, 22, 14, 36, 36, 14, 22],
    )
    _append_rows(summary, rows)

    atomic = workbook.create_sheet("原子子要求明细")
    atomic_headers = [
        "参数序号",
        "参数名称",
        "子要求序号",
        "子要求原文",
        "逻辑关系",
        "必要性",
        "原子结论",
        "判断原因",
        "评估方法",
        "证据编号",
        "证据来源",
        "来源位置",
        "适用条件",
        "证据限制",
        "证据是否可定位",
    ]
    atomic_rows = []
    for item in data["parameters"]:
        for atom in item["atoms"]:
            evidence = atom.get("evidence") or []
            atomic_rows.append(
                [
                    item["order_index"],
                    item["parameter_name"],
                    atom["order_index"],
                    atom["requirement_text"],
                    atom.get("logical_relation") or "AND",
                    atom.get("mandatory_status")
                    or ("required" if atom.get("necessary") else "optional"),
                    atom["verdict"],
                    atom.get("reason") or "",
                    atom.get("assessment_method") or "",
                    "、".join(str(e.get("evidence_id")) for e in evidence),
                    "；".join(
                        f"{source_type_label(e.get('kind'))} {e.get('source_file') or ''}"
                        for e in evidence
                    ),
                    "；".join(str(e.get("locator_text") or "") for e in evidence),
                    "；".join(
                        str(e.get("conditions_text") or "")
                        for e in evidence
                        if e.get("conditions_text")
                    )
                    or atom.get("conditions_text", ""),
                    "；".join(
                        str(e.get("limitations_text") or "")
                        for e in evidence
                        if e.get("limitations_text")
                    ),
                    atom.get("evidence_located") or "无证据",
                ]
            )
    _style_sheet(
        atomic,
        atomic_headers,
        [10, 24, 10, 46, 10, 10, 12, 48, 14, 30, 36, 28, 30, 34, 16],
    )
    _append_rows(atomic, atomic_rows)

    issues = workbook.create_sheet("待确认与问题清单")
    issue_headers = [
        "参数序号",
        "参数名称",
        "问题类型",
        "具体说明",
        "涉及子要求",
        "建议核实事项",
        "复核状态",
        "人工应答结论",
    ]
    issue_rows = [
        [
            item["order_index"],
            item["parameter_name"],
            item["issue_type"],
            item["detail"],
            item["atom_refs"],
            item["suggestion"],
            item["review_status_label"],
            item["human_final_verdict"],
        ]
        for item in data["issues"]
    ]
    _style_sheet(issues, issue_headers, [10, 24, 22, 60, 14, 52, 22, 14])
    _append_rows(issues, issue_rows)

    evidence_sheet = workbook.create_sheet("证据索引")
    evidence_headers = [
        "证据编号",
        "来源类型",
        "来源文件",
        "位置",
        "引文（节选）",
        "核验状态",
        "能力名称",
        "适用范围",
        "限制条件",
        "关联参数",
        "证据上下文状态",
    ]
    evidence_rows = [
        [
            item.get("evidence_id", ""),
            source_type_label(item.get("kind")),
            item.get("source_file") or "",
            item.get("locator_text") or "",
            item.get("quote") or "",
            item.get("verification_state") or "",
            item.get("capability_name") or "",
            item.get("supported_scope_text") or "",
            item.get("limitations_text") or "",
            "；".join(item.get("used_for") or []),
            item.get("context_status") or "",
        ]
        for item in data["evidence_index"]
    ]
    _style_sheet(
        evidence_sheet,
        evidence_headers,
        [30, 12, 40, 28, 70, 18, 34, 40, 40, 40, 18],
    )
    _append_rows(evidence_sheet, evidence_rows)

    workbook.save(path)


def create_export(db, job: EvaluationJob, principal_id: str, fmt: str):
    from secrets import token_urlsafe
    from uuid import uuid4

    settings = get_settings()
    if job.status not in REVIEWABLE:
        raise AppError(
            409,
            "EXPORT_JOB_NOT_READY",
            "只有评估成功或部分成功的任务才能导出",
        )
    active_count = (
        db.scalar(
            select(func.count(EvaluationExportJob.id)).where(
                EvaluationExportJob.principal_id == principal_id,
                EvaluationExportJob.status.in_(["queued", "running"]),
            )
        )
        or 0
    )
    if active_count >= settings.export_max_active_jobs:
        raise AppError(429, "EXPORT_ACTIVE_LIMIT", "同时生成的导出任务已达上限")
    export = EvaluationExportJob(
        job_id=job.id,
        principal_id=principal_id,
        format=fmt,
        status="queued",
        download_token=token_urlsafe(32),
        download_expires_at=datetime.now(UTC)
        + timedelta(seconds=settings.export_download_ttl_seconds),
        dispatch_token=uuid4().hex,
        summary_json={},
    )
    db.add(export)
    db.flush()
    export.filename = f"evaluation-report-{job.id[:8]}-{export.id[:8]}.{fmt}"
    db.add(
        AuditEvent(
            principal_id=(principal_id or "unknown")[:32],
            action="evaluation.export.create",
            resource_id=job.id,
            correlation_id=export.id,
            details_json={
                "export_id": export.id,
                "format": fmt,
                "publication_id": job.publication_id,
            },
        )
    )
    db.commit()
    return export


def run_export_job(export_id: str, dispatch_token: str | None = None):
    settings = get_settings()
    with session_factory()() as db:
        export = db.get(EvaluationExportJob, export_id)
        if not export or export.status != "queued":
            return
        if dispatch_token and export.dispatch_token != dispatch_token:
            return
        export.status = "running"
        export.started_at = datetime.now(UTC)
        export.dispatch_token = None
        db.commit()
        path = None
        try:
            data = report_data(db, export)
            store = Path(settings.export_store)
            store.mkdir(parents=True, exist_ok=True, mode=0o700)
            storage_key = f"{export.id}.{export.format}"
            path = store / storage_key
            temp = store / f".tmp-{export.id}"
            if export.format == "docx":
                write_docx(data, temp)
            else:
                write_xlsx(data, temp)
            size = temp.stat().st_size
            if size > settings.export_max_file_bytes:
                temp.unlink(missing_ok=True)
                raise AppError(413, "EXPORT_FILE_TOO_LARGE", "导出文件超过大小上限")
            os.chmod(temp, 0o600)
            generated_sha256 = file_sha256(temp)
            os.replace(temp, path)
            export.status = "succeeded"
            export.storage_key = storage_key
            export.file_sha256 = generated_sha256
            export.size_bytes = size
            export.parameter_count = data["parameter_count"]
            export.atomic_count = data["atomic_count"]
            export.evidence_count = data["evidence_count"]
            export.review_count = data["review_count"]
            export.summary_json = {
                "counts": data["counts"],
                "failed_count": data["failed_count"],
                "parameter_count": data["parameter_count"],
                "atomic_count": data["atomic_count"],
                "evidence_count": data["evidence_count"],
                "review_count": data["review_count"],
                "publication_id": data["publication_id"],
                "publication_hash": data["publication_hash"],
                "data_digest": data["data_digest"],
                "file_sha256": generated_sha256,
            }
            export.finished_at = datetime.now(UTC)
            export.error_code = None
            db.commit()
        except AppError as exc:
            db.rollback()
            export = db.get(EvaluationExportJob, export_id)
            if export:
                export.status = "failed"
                export.error_code = exc.error_code
                export.finished_at = datetime.now(UTC)
                db.commit()
            if path:
                Path(path).unlink(missing_ok=True)
        except Exception:
            db.rollback()
            export = db.get(EvaluationExportJob, export_id)
            if export:
                export.status = "failed"
                export.error_code = "EXPORT_GENERATION_FAILED"
                export.finished_at = datetime.now(UTC)
                db.commit()
            if path:
                Path(path).unlink(missing_ok=True)


def schedule_export(export_id: str, token: str | None, background_tasks):
    settings = get_settings()
    if settings.export_queue_enabled:
        try:
            from rq import Queue

            from app.jobs.queue import connection

            Queue(settings.export_queue_name, connection=connection()).enqueue(
                run_export_job,
                export_id,
                token,
                job_timeout=settings.export_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=86400,
            )
            return
        except Exception:
            pass
    background_tasks.add_task(run_export_job, export_id, token)


def as_utc(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def token_fingerprint(token: str | None):
    if not token:
        return ""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_json(export: EvaluationExportJob):
    expires_at = as_utc(export.download_expires_at)
    return {
        "export_id": export.id,
        "job_id": export.job_id,
        "format": export.format,
        "status": export.status,
        "filename": export.filename,
        "size_bytes": export.size_bytes,
        "parameter_count": export.parameter_count,
        "atomic_count": export.atomic_count,
        "evidence_count": export.evidence_count,
        "review_count": export.review_count,
        "summary": export.summary_json or {},
        "error_code": export.error_code,
        "download_token": export.download_token,
        "download_expires_at": expires_at,
        "download_expired": bool(expires_at and expires_at <= datetime.now(UTC)),
        "download_url": f"/api/v1/tender/workbench/exports/{export.id}/download",
        "file_sha256": export.file_sha256,
        "integrity_available": bool(export.file_sha256),
        "created_at": export.created_at,
        "started_at": export.started_at,
        "finished_at": export.finished_at,
    }
