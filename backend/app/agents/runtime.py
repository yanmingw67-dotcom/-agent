"""持久化预算、模块检查点和脱敏模型/工具轨迹。"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Module
from app.db.knowledge_models import CapabilityClaim, Investigation, InvestigationTrace
from app.db.models import KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.ingestion.resources import ResourceSampler
from app.ingestion.text import digest
from app.knowledge.classification import classification as trusted_classification
from app.knowledge.evidence_registry import canonical, register
from app.knowledge.model_authorization import CLASSIFICATIONS, require_model_authorization
from app.knowledge.schemas import EvidenceRequest
from app.knowledge.service import fence, save_output, validate_output
from app.retrieval.schemas import RunContext, ToolRequest
from app.retrieval.tools import ReadOnlyTools


def configured_model(classification):
    s = get_settings()
    if not s.builder_model or not s.builder_base_url or not s.builder_api_key.get_secret_value():
        raise AppError(503, "MODEL_NOT_CONFIGURED", "未配置企业批准模型")
    # 环境变量只是运行时开关；真实外发前必须命中数据库中的正式授权记录。
    with session_factory()() as db:
        require_model_authorization(db, classification)
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIChatModel(
        s.builder_model,
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=s.builder_base_url,
                api_key=s.builder_api_key.get_secret_value(),
                max_retries=0,
                timeout=60,
            )
        ),
    )


@dataclass
class BuilderDeps:
    run_id: str
    token: str
    context: RunContext
    reads: set = field(default_factory=set)
    gaps: set = field(default_factory=set)
    data_classification: str = "enterprise"
    # 测试注入模型不访问外部端点；生产 configured_model 必须逐请求复核授权。
    enforce_model_authorization: bool = True

    def update(self, kind, details, **increments):
        with session_factory().begin() as db:
            row = fence(db, self.run_id, self.token)
            metrics = dict(row.metrics_json)
            for key, value in increments.items():
                metrics[key] = metrics.get(key, 0) + value
            limits = {
                "tool_calls": get_settings().builder_max_tool_calls,
                "model_requests": get_settings().builder_max_requests,
                "context_chars": get_settings().builder_max_context_chars,
                "tokens": get_settings().builder_max_tokens,
            }
            if any(metrics.get(k, 0) > limit for k, limit in limits.items()):
                raise AppError(
                    429, "BUILDER_BUDGET_EXCEEDED", "模块累计预算耗尽；须创建新任务并审查预算"
                )
            job = db.get(KnowledgeJob, row.job_id)
            if job.metrics_json.get("investigation_mode") == "autonomous":
                if increments.get("model_requests") or increments.get("tool_calls"):
                    started = datetime.fromisoformat(job.metrics_json["exploration_started_at"])
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    if (
                        datetime.now(timezone.utc) - started
                    ).total_seconds() >= get_settings().builder_module_timeout_seconds:
                        raise AppError(429, "BUILDER_TASK_TIME_EXCEEDED", "自主任务时间预算耗尽")
                rows = db.scalars(select(Investigation).where(Investigation.job_id == job.id)).all()
                for key, limit in limits.items():
                    total = metrics.get(key, 0) + sum(
                        r.metrics_json.get(key, 0) for r in rows if r.id != row.id
                    )
                    if total > limit:
                        raise AppError(429, "BUILDER_TASK_BUDGET_EXCEEDED", "自主任务累计预算耗尽")
            seq = metrics.get("trace_sequence", 0) + 1
            metrics["trace_sequence"] = seq
            row.metrics_json = metrics
            db.add(
                InvestigationTrace(
                    investigation_id=row.id,
                    attempt=row.attempt,
                    sequence=seq,
                    kind=kind,
                    details_json=details,
                )
            )

    def consume(self, name, args):
        self.update(
            "tool_call", {"tool": name, "args_hash": digest(canonical(args).encode())}, tool_calls=1
        )

    def query(self, request):
        self.consume(request.tool, request.model_dump())
        try:
            with session_factory()() as db:
                result = ReadOnlyTools(db, self.context).execute(request)
            encoded = result.model_dump_json()
            self.update(
                "tool_result",
                {
                    "tool": request.tool,
                    "result_hash": digest(encoded.encode()),
                    "result_count": len(result.results),
                    "truncated": result.truncated,
                    "coverage_incomplete": result.coverage_incomplete,
                    "unresolved": any(i.unresolved for i in result.results),
                },
                context_chars=len(encoded),
            )
            if result.truncated:
                self.gaps.add(f"{request.tool}:truncated；未穷尽结果或上下文")
            if result.coverage_incomplete:
                self.gaps.add(f"{request.tool}:coverage_incomplete；不得用无命中判不支持")
            if any(i.unresolved for i in result.results):
                self.gaps.add(f"{request.tool}:unresolved；语法候选不证明可达性")
            if not result.truncated:
                for item in result.results:
                    if request.tool == "read_source":
                        self.reads.add(
                            ("source", item.file_id, item.start_line, item.end_line, item.sha256)
                        )
                    elif request.tool == "read_document":
                        self.reads.add(("document", item.id, 1, 1, item.sha256))
            return result
        except AppError as exc:
            self.update("tool_error", {"tool": request.tool, "error_code": exc.error_code})
            raise

    def register(self, request: EvidenceRequest):
        self.consume("register_evidence", request.model_dump())
        key = (
            (
                request.kind,
                request.file_id,
                request.start_line,
                request.end_line,
                request.expected_sha256,
            )
            if request.kind == "source"
            else (request.kind, request.segment_id, 1, 1, request.expected_sha256)
        )
        if key not in self.reads:
            raise AppError(422, "EVIDENCE_NOT_READ", "登记前必须完整回读相同定位和哈希")
        with session_factory().begin() as db:
            row = fence(db, self.run_id, self.token)
            receipt = register(db, self.context, request)
            row.evidence_ids = sorted(set(row.evidence_ids + [receipt.evidence_id]))
        self.update(
            "evidence_registered",
            {"evidence_id": receipt.evidence_id, "quote_hash": receipt.quote_hash},
        )
        return receipt

    def candidates(self, query, limit):
        self.consume("get_capability_candidates", {"query": query, "limit": limit})
        if not 1 <= limit <= 20 or len(query) > 200:
            raise AppError(422, "CANDIDATE_QUERY_INVALID", "候选查询超限")
        with session_factory()() as db:
            rows = db.scalars(
                select(CapabilityClaim)
                .where(
                    CapabilityClaim.snapshot_id == self.context.snapshot_id,
                    CapabilityClaim.name.contains(query, autoescape=True),
                )
                .order_by(CapabilityClaim.id)
                .limit(limit + 1)
            ).all()
            result = {
                "snapshot_id": self.context.snapshot_id,
                "truncated": len(rows) > limit,
                "unresolved": True,
                "results": [
                    {"id": r.id, "status": r.status, "draft": r.draft_json} for r in rows[:limit]
                ],
            }
        self.update(
            "tool_result",
            {"tool": "get_capability_candidates", "result_count": len(rows[:limit])},
            context_chars=len(canonical(result)),
        )
        if result["truncated"]:
            self.gaps.add("候选能力查询truncated")
        return result

    def validate(self, output):
        with session_factory()() as db:
            row = db.get(Investigation, self.run_id)
            output = validate_output(db, self.context, row, output)
        output.open_questions = list(dict.fromkeys(sorted(self.gaps) + output.open_questions))[:30]
        return output


class AuditedModel(WrapperModel):
    def __init__(self, model, deps):
        super().__init__(model)
        self.deps = deps

    def _record_authorization_denied(self, error_code):
        try:
            self.deps.update(
                "model_authorization_denied",
                {
                    "stage": "pre_request",
                    "error_code": error_code,
                    "data_classification": self.deps.data_classification,
                },
            )
        except Exception:
            # 审计写入失败不能把已拒绝的请求放行；保留原始授权错误。
            pass

    def _require_live_authorization(self):
        if not self.deps.enforce_model_authorization:
            return
        try:
            with session_factory()() as db:
                require_model_authorization(db, self.deps.data_classification)
        except AppError as exc:
            self._record_authorization_denied(exc.error_code)
            raise

    async def request(self, messages, model_settings, model_request_parameters):
        # 授权可能在任务启动后被撤销、到期或端点变更；每次外发前都重新读取数据库事实。
        self._require_live_authorization()
        if len(str(messages)) > get_settings().builder_max_context_chars + 20000:
            raise AppError(429, "BUILDER_CONTEXT_LIMIT", "模型上下文大小超限")
        self.deps.update(
            "model_request",
            {
                "message_count": len(messages),
                "reasoning_effort": (model_settings or {}).get("openai_reasoning_effort"),
            },
            model_requests=1,
        )
        response = await super().request(messages, model_settings, model_request_parameters)
        usage = response.usage
        # 先持久化实际已消耗的Token，即使超额/输出无效也不丢失计量。
        with session_factory().begin() as db:
            row = fence(db, self.deps.run_id, self.deps.token)
            m = dict(row.metrics_json)
            m["input_tokens"] = m.get("input_tokens", 0) + usage.input_tokens
            m["output_tokens"] = m.get("output_tokens", 0) + usage.output_tokens
            m["tokens"] = m["input_tokens"] + m["output_tokens"]
            m["model_responses"] = m.get("model_responses", 0) + 1
            m["unmeasured_requests"] = m.get("unmeasured_requests", 0) + usage.unmeasured_requests
            row.metrics_json = m
        self.deps.update(
            "model_response",
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.output_reasoning_tokens,
                "finish_reason": response.finish_reason,
                "parts": [
                    {
                        "kind": p.part_kind,
                        "tool": getattr(p, "tool_name", None)
                        if getattr(p, "tool_name", None)
                        in {
                            "query_knowledge",
                            "register_evidence",
                            "get_capability_candidates",
                            "final_result",
                            "register_evidence_and_candidate",
                            "record_hypothesis",
                            "plan_investigation",
                            "finish_fragment",
                            "assess_equivalence",
                        }
                        else None,
                        "tool_call_id_hash": digest(str(getattr(p, "tool_call_id", "")).encode()),
                    }
                    for p in response.parts
                ],
            },
        )
        return response

    @asynccontextmanager
    async def request_stream(self, *args, **kwargs):
        # 流式调用同样属于外发路径，不能绕过逐请求授权复核。
        self._require_live_authorization()
        async with super().request_stream(*args, **kwargs) as response:
            yield response


async def investigate_module(
    run_id, token, model, data_classification=None, enforce_authorization=True
):
    from app.agents.knowledge_builder import AgentToolEnvelope, make_agent

    s = get_settings()
    with session_factory().begin() as db:
        row = fence(db, run_id, token)
        row.status = "running"
        row.attempt += 1
        job = db.get(KnowledgeJob, row.job_id)
        ctx = RunContext(
            category_id=job.category_id,
            snapshot_id=job.snapshot_id,
            principal_id="admin",
            job_id=job.id,
            policy="admin_staging",
        )
        module_id = row.module_id
        mode = job.metrics_json.get("investigation_mode", "explore")
        snapshot_id = job.snapshot_id
        classification = data_classification or job.metrics_json.get("data_classification")
    if classification not in CLASSIFICATIONS:
        with session_factory()() as db:
            classification = trusted_classification(db, snapshot_id)
    deps = BuilderDeps(
        run_id,
        token,
        ctx,
        data_classification=classification,
        enforce_model_authorization=enforce_authorization,
    )
    if mode == "bounded":
        from app.agents.bounded_builder import investigate_bounded

        return await investigate_bounded(deps, module_id, model)
    if mode == "autonomous":
        from app.agents.autonomous import investigate_autonomous

        return await investigate_autonomous(deps, module_id, model)
    # 预调查也计入工具/上下文预算，不将整库塞入提示。
    seed = [
        deps.query(ToolRequest(tool="get_module_context", module_id=module_id, limit=20)),
        deps.query(ToolRequest(tool="list_entrypoints", module_id=module_id, limit=30)),
    ]
    with session_factory()() as db:
        name = db.get(Module, module_id).name
    documents = deps.query(ToolRequest(tool="search_documents", query=name, limit=5))
    seed.append(documents)
    if not documents.results:
        fallback = deps.query(ToolRequest(tool="search_documents", query="", limit=5))
        seed.append(fallback)
        if fallback.results:
            deps.gaps.add("文档为同快照小批候选回退；模块相关性与覆盖率未验证")
    prompt = canonical(
        {
            "module_id": module_id,
            "task": "模块级功能/机制及潜在等效调查，只有候选",
            "untrusted_seed_data": [
                AgentToolEnvelope.model_validate(r.model_dump()).model_dump() for r in seed
            ],
        }
    )
    agent = make_agent(BuilderDeps)
    model_settings = {"max_tokens": s.builder_max_output_tokens, "timeout": 60, "max_retries": 0}
    if s.builder_reasoning_effort:
        model_settings["openai_reasoning_effort"] = s.builder_reasoning_effort
    result = await agent.run(
        prompt,
        model=AuditedModel(model, deps),
        deps=deps,
        usage_limits=UsageLimits(
            request_limit=s.builder_max_requests,
            tool_calls_limit=s.builder_max_tool_calls,
            total_tokens_limit=s.builder_max_tokens,
        ),
        model_settings=model_settings,
        timeout=s.builder_module_timeout_seconds,
    )
    normalized = deps.validate(result.output)
    with session_factory().begin() as db:
        saved = save_output(db, ctx, run_id, token, normalized)
        saved["coverage_incomplete"] = bool(deps.gaps)
        db.get(Investigation, run_id).output_json = dict(saved)
        return saved


def run_builder(job_id, token, model=None):
    s = get_settings()
    started = time.monotonic()
    sampler = ResourceSampler()
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.dispatch_token != token:
            return
        snapshot = db.get(Snapshot, job.snapshot_id)
        if snapshot.status == "published" or snapshot.index_status != "indexed":
            job.status = job.stage = "failed"
            job.error_code = "SOURCE_INDEX_NOT_READY"
            job.finished_at = now()
            return
        classification = trusted_classification(db, job.snapshot_id)
        job.metrics_json = {**job.metrics_json, "data_classification": classification}
        job.status = "running"
        job.stage = "investigating"
        job.started_at = now()
        if job.metrics_json.get("investigation_mode") == "autonomous":
            job.metrics_json = {
                **job.metrics_json,
                "exploration_started_at": job.metrics_json.get("exploration_started_at")
                or now().isoformat(),
            }
        ids = list(
            db.scalars(
                select(Investigation.id)
                .where(Investigation.job_id == job_id, Investigation.status != "succeeded")
                .order_by(Investigation.module_id)
            )
        )
    sampler.start()
    fatal = None
    # 同一模型的异步HTTP连接池不能跨多个已关闭的事件循环复用。
    runner = asyncio.Runner()
    try:
        actual_model = model if model is not None else configured_model(classification)
        for rid in ids:
            try:
                runner.run(
                    asyncio.wait_for(
                        investigate_module(
                            rid,
                            token,
                            actual_model,
                            classification,
                            # 仅本地 FunctionModel 不访问外部端点；其他任何模型都必须复核授权。
                            enforce_authorization=not isinstance(actual_model, FunctionModel),
                        ),
                        timeout=s.builder_module_timeout_seconds,
                    )
                )
            except Exception as exc:
                code = (
                    exc.error_code
                    if isinstance(exc, AppError)
                    else (
                        "BUILDER_BUDGET_EXCEEDED"
                        if type(exc).__name__ == "UsageLimitExceeded"
                        else "BUILDER_TIMEOUT"
                        if isinstance(exc, TimeoutError)
                        or type(exc).__name__ in {"ModelTimeoutError", "RunTimedOut"}
                        else "BUILDER_OUTPUT_LIMIT"
                        if type(exc).__name__ == "OutputTokenLimitExceeded"
                        else "MODEL_OR_OUTPUT_ERROR"
                    )
                )
                with session_factory().begin() as db:
                    row = fence(db, rid, token)
                    row.status = "failed"
                    row.error_code = code
                    if row.metrics_json.get("exploration"):
                        state = dict(row.metrics_json["exploration"])
                        state["stop_reason"] = code
                        state["coverage_incomplete"] = True
                        from app.agents.autonomous import coverage

                        state["coverage"] = coverage(state)
                        row.metrics_json = {**row.metrics_json, "exploration": state}
                        row.output_json = {
                            **row.output_json,
                            "knowledge_build_complete": False,
                            "coverage_incomplete": True,
                            "stop_reason": code,
                        }
                        row.status = "partial"
                    metrics = dict(row.metrics_json)
                    seq = metrics.get("trace_sequence", 0) + 1
                    metrics["trace_sequence"] = seq
                    row.metrics_json = metrics
                    db.add(
                        InvestigationTrace(
                            investigation_id=rid,
                            attempt=row.attempt,
                            sequence=seq,
                            kind="module_error",
                            details_json={"error_code": code, "exception_type": type(exc).__name__},
                        )
                    )
                # 保留成功模块检查点；失败模块重试不重复落入能力结果。
    except Exception as exc:
        fatal = exc.error_code if isinstance(exc, AppError) else "BUILDER_INTERNAL_ERROR"
    finally:
        runner.close()
    resource = sampler.finish()
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if job.dispatch_token != token or job.status != "running":
            return
        runs = db.scalars(select(Investigation).where(Investigation.job_id == job_id)).all()
        totals = {
            k: sum(r.metrics_json.get(k, 0) for r in runs)
            for k in [
                "model_requests",
                "model_responses",
                "unmeasured_requests",
                "tool_calls",
                "tokens",
                "input_tokens",
                "output_tokens",
            ]
        }
        complete = sum(r.status == "succeeded" for r in runs)
        unresolved = (
            sum(len(r.output_json.get("output", {}).get("open_questions", [])) for r in runs)
            + len(runs)
            - complete
        )
        costs = None
        if (
            s.builder_input_usd_per_million is not None
            and s.builder_output_usd_per_million is not None
            and totals["model_requests"] == totals["model_responses"]
            and not totals["unmeasured_requests"]
        ):
            costs = (
                totals["input_tokens"] * s.builder_input_usd_per_million
                + totals["output_tokens"] * s.builder_output_usd_per_million
            ) / 1_000_000
        job.metrics_json = {
            **job.metrics_json,
            **totals,
            **resource,
            "completed_modules": complete,
            "unresolved_count": unresolved,
            "uncovered_modules": len(runs) - complete,
            "claimed_capabilities": sum(len(r.output_json.get("claim_ids", {})) for r in runs),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "model_cost_usd": costs,
            "cost_basis": "configured_token_rates_estimate"
            if costs is not None
            else "unknown_not_zero",
            "usage_incomplete": totals["model_requests"] != totals["model_responses"]
            or bool(totals["unmeasured_requests"]),
            "model_mode": "TEST_FUNCTION_MODEL"
            if model is not None
            else ("NOT_STARTED" if not totals["model_requests"] else "REAL"),
            "model": s.builder_model if model is None else "injected_test_model",
            **(
                {"knowledge_build_complete": False, "coverage_incomplete": True}
                if job.metrics_json.get("investigation_mode") == "autonomous"
                else {}
            ),
        }
        job.status = (
            "failed"
            if fatal
            or (
                not complete
                and not job.metrics_json["claimed_capabilities"]
                and not any(r.status == "partial" for r in runs)
            )
            else ("succeeded" if complete == len(runs) else "partial")
        )
        job.stage = "review_ready" if job.status == "succeeded" else job.status
        runtime_errors = [r.error_code for r in runs if r.error_code]
        if job.metrics_json.get("investigation_mode") == "autonomous":
            reasons = [r.metrics_json.get("exploration", {}).get("stop_reason") for r in runs]
            partial_reason = (
                "failed"
                if fatal or runtime_errors
                else "fragment_budget"
                if "fragment_budget" in reasons
                else "task_budget"
                if any(str(reason).startswith("task_") for reason in reasons)
                else "dependency_gap"
                if job.status == "partial"
                else None
            )
            job.metrics_json = {**job.metrics_json, "partial_reason": partial_reason}
            job.error_code = fatal or (runtime_errors[0] if runtime_errors else None)
        else:
            job.error_code = fatal or (
                None if job.status == "succeeded" else "MODULE_INVESTIGATION_FAILED"
            )
        job.finished_at = now()
