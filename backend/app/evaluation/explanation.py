"""P2.2 中文结论说明：由三值结果和证据计数确定性生成，不调用自由文本模型。"""

from app.evaluation.logic import SUPPORTED, UNKNOWN, UNSUPPORTED


def atom_reason(
    *,
    verdict: str,
    supported: list[str],
    contradictions: list[str],
    gaps: list[str],
    ambiguity: str | None,
    evidence_count: int,
) -> str:
    if ambiguity == "blank_threshold":
        return "参数阈值留空，原文未给出可核验数值；不猜测数值，按无法确认处理。"
    if ambiguity == "logic_ambiguity":
        return "原子要求的逻辑结构存在歧义，无法确定必要组合关系；按无法确认处理。"
    if ambiguity == "open_enumeration":
        return "开放枚举未明确全部必要项，需人工确认范围；不作为不支持。"
    if verdict == SUPPORTED:
        source = supported[0] if supported else "已发布知识"
        return f"已发布知识“{source}”的已核验证据支持该原子要求（证据 {evidence_count} 条）。"
    if verdict == UNSUPPORTED:
        claim = contradictions[0] if contradictions else "已发布限制说明"
        return f"已发布知识存在明确否定或范围限制“{claim}”，且已核验证据覆盖该要求范围，因此判为不支持。"
    if supported and contradictions:
        return "同时存在支持证据与明确限制证据，冲突未解决；按无法确认处理，不判为不支持。"
    if gaps:
        return (
            f"未找到可用的已发布知识证据（{gaps[0]}）；没有证据不等于产品不支持，按无法确认处理。"
        )
    return "未找到能够覆盖该原子要求的已核验公开知识；按无法确认处理，不判为不支持。"


def parameter_rationale(
    *,
    parameter_name: str,
    verdict: str,
    aggregate: dict,
    atom_results: list[dict],
    publication_id: str,
) -> str:
    counts = aggregate.get("counts") or {}
    supported = counts.get(SUPPORTED, 0)
    unsupported = counts.get(UNSUPPORTED, 0)
    unknown = counts.get(UNKNOWN, 0)
    total = sum(counts.values())
    if verdict == SUPPORTED:
        head = (
            f"参数“{parameter_name}”判为支持。必要原子要求共 {total} 项，"
            f"{supported} 项由固定发布知识中的已核验证据支持，未发现明确否定证据。"
        )
    elif verdict == UNSUPPORTED:
        negatives = [
            item.get("requirement_text", "")
            for item in atom_results
            if item.get("necessary", True) and item.get("verdict") == UNSUPPORTED
        ]
        sample = "；".join(negatives[:2])
        head = (
            f"参数“{parameter_name}”判为不支持。必要原子要求共 {total} 项，"
            f"{unsupported} 项存在明确否定或范围限制证据"
            + (f"（如：{sample}）" if sample else "")
            + "。"
        )
    else:
        uncertain = aggregate.get("uncertain_requirement_ids") or []
        if uncertain:
            head = (
                f"参数“{parameter_name}”判为无法确认。必要原子要求共 {total} 项，"
                f"其中 {unknown} 项证据不足，且存在待人工确认的开放枚举或逻辑范围，不能推定支持或不支持。"
            )
        elif unknown:
            head = (
                f"参数“{parameter_name}”判为无法确认。必要原子要求共 {total} 项，"
                f"{supported} 项有支持证据，{unknown} 项缺少可核验的已发布知识证据；"
                "没有证据不等于产品不支持，因此不判为不支持。"
            )
        else:
            head = (
                f"参数“{parameter_name}”判为无法确认。原子要求或逻辑结构不完整，"
                "服务端无法按确定规则聚合出支持或不支持。"
            )
    details = []
    for item in atom_results:
        if not item.get("necessary", True):
            continue
        details.append(f"{item.get('verdict')}：{item.get('requirement_text', '')[:80]}")
    tail = f"固定发布版本：{publication_id}；本结论未使用投标证明材料要求。"
    body = " 原子判断：" + "；".join(details[:6]) + "。" if details else ""
    return (head + body + " " + tail)[:2400]
