"""回读双方实现后比较契约；拒绝等效和信息不足也是调查结果。"""

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, ToolOutput
from sqlalchemy import select

from app.agents.autonomous import ask, load_state, persist
from app.db.index_models import CodeSymbol
from app.db.knowledge_models import Investigation
from app.db.session import session_factory
from app.knowledge.evidence_registry import read_evidence
from app.knowledge.schemas import CandidateRelation, ModuleInvestigationOutput, Strict
from app.knowledge.service import fence, save_output
from app.retrieval.schemas import ToolRequest


class Comparison(Strict):
    source_key: str
    target_key: str
    verdict: Literal["not_equivalent", "insufficient_evidence", "possible_equivalent"]
    input_contract: Annotated[str, Field(min_length=1, max_length=300)]
    output_contract: Annotated[str, Field(min_length=1, max_length=300)]
    conditions_and_errors: Annotated[str, Field(min_length=1, max_length=500)]
    rationale: Annotated[str, Field(min_length=1, max_length=500)]
    gaps: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=4
    )


async def investigate_equivalence(deps, model):
    state = load_state(deps) or {"tasks": [], "equivalence": {"status": "pending"}}
    if state["equivalence"]["status"] == "investigated":
        return
    with session_factory()() as db:
        row = db.get(Investigation, deps.run_id)
        raw = row.output_json.get("output")
        if not raw or len(raw.get("capabilities", [])) < 2:
            state["equivalence"] = {"status": "deferred", "reason": "need_two_evidenced_candidates"}
            persist(deps, state)
            return
        output = ModuleInvestigationOutput.model_validate(raw)
        # 用最近两条不同源码候选比较；不按名字相似度挑选，更不据此判等效。
        chosen = output.capabilities[-2:]
        evidence = {}
        for claim in chosen:
            for eid in claim.evidence_ids:
                evidence[eid] = read_evidence(db, deps.context, eid)
    sources = {}
    for eid, record in evidence.items():
        if record["kind"] != "source":
            continue
        loc = record["locator"]
        result = deps.query(
            ToolRequest(
                tool="read_source",
                file_id=record["source_ref"]["file_id"],
                start_line=loc["start_line"],
                end_line=loc["end_line"],
            )
        )
        if result.truncated or not result.results:
            return
        sources[eid] = result.results[0].model_dump(exclude_none=True)
    if len(sources) < 2:
        state["equivalence"] = {"status": "deferred", "reason": "need_two_source_locations"}
        persist(deps, state)
        return

    async def assess_equivalence(ctx: RunContext, comparison: Comparison) -> str:
        keys = {c.key for c in chosen}
        if {comparison.source_key, comparison.target_key} != keys:
            raise ModelRetry("必须比较给定两个不同候选的key")
        state["equivalence"] = {
            "status": "investigated",
            **comparison.model_dump(),
            "evidence_ids": list(sources),
            "source_locations": list(sources.values()),
            "semantic_verification": "unverified",
        }
        if comparison.verdict == "possible_equivalent":
            functions = set()
            with session_factory()() as db:
                for source in sources.values():
                    symbol = db.scalar(
                        select(CodeSymbol)
                        .where(
                            CodeSymbol.file_id == source["file_id"],
                            CodeSymbol.start_line <= source["start_line"],
                            CodeSymbol.end_line >= source["end_line"],
                        )
                        .order_by(CodeSymbol.start_line.desc())
                        .limit(1)
                    )
                    functions.add((source["file_id"], symbol.id if symbol else None))
            if len(functions) < 2:
                raise ModelRetry("双方证据须来自不同函数或模块；同一函数不同描述不足以建立等效假设")
            output.candidate_relations.append(
                CandidateRelation(
                    source_key=comparison.source_key,
                    target_key=comparison.target_key,
                    relation_type="possible_equivalent",
                    rationale=comparison.rationale
                    + "；输入："
                    + comparison.input_contract
                    + "；输出："
                    + comparison.output_contract,
                    evidence_ids=list(sources),
                    unverified_gaps=comparison.gaps,
                )
            )
            with session_factory().begin() as db:
                row = fence(db, deps.run_id, deps.token)
                save_output(db, deps.context, deps.run_id, deps.token, output, finalize=False)
                row.metrics_json = {**row.metrics_json, "exploration": state}
        else:
            persist(ctx.deps, state, "equivalence_counterexample")
        ctx.deps.update(
            "equivalence_assessed",
            {
                "verdict": comparison.verdict,
                "evidence_ids": list(sources),
                "source_key": comparison.source_key,
                "target_key": comparison.target_key,
            },
        )
        return "hypothesis_or_counterexample_only"

    await ask(
        deps,
        model,
        ToolOutput(assess_equivalence, name="assess_equivalence"),
        {"candidates": [c.model_dump() for c in chosen], "actual_source": sources},
        "根据双方真实源码比较输入/输出、参数配置、前提、异常、副作用及调用关系。"
        "同名或同为去重/缓存/重试不代表可互换；组成/调用关系不等于等效。"
        "若机制或适用范围不同明确not_equivalent；证据不够选insufficient_evidence。"
        "只有有具体契约和条件对应才可possible_equivalent，仍仅假设，不批准。"
        "每个契约/解释不超过100字，gaps最多2条每条50字；直接输出工具参数，不复述整个候选。",
    )
