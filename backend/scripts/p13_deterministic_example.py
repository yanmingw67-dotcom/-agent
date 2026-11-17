"""真实PostgreSQL/源码索引/证据登记；仅模型为FunctionModel，不能替代真实模型验收。"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p13")
REMOTE = r"""
import json,sys
from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse,ToolCallPart,ToolReturnPart
from sqlalchemy import select
from app.db.models import KnowledgeJob
from app.db.knowledge_models import Investigation,InvestigationTrace,CapabilityClaim
from app.db.session import session_factory
from app.jobs.queue import connection
from app.agents.runtime import run_builder
from app.retrieval.schemas import RunContext
from app.knowledge.evidence_registry import read_evidence

models.ALLOW_MODEL_REQUESTS=False
cid,sid,mid,file_id,sha,start,end=sys.argv[1:]
start,end=int(start),int(end)
async def respond(messages,info):
    returns=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart)]
    if not returns:
        return ModelResponse(parts=[ToolCallPart("query_knowledge",{"request":{
            "tool":"read_source","file_id":file_id,"start_line":start,"end_line":end}})])
    if returns[-1].tool_name=="query_knowledge":
        return ModelResponse(parts=[ToolCallPart("register_evidence",{"request":{
            "kind":"source","file_id":file_id,"start_line":start,"end_line":end,
            "expected_sha256":sha,"supported_statement":"固定平台文本载荷格式化与多消息拼接机制"}})])
    receipt=returns[-1].content
    if hasattr(receipt,"model_dump"): receipt=receipt.model_dump()
    if isinstance(receipt,str): receipt=json.loads(receipt)
    eid=receipt["evidence_id"]
    claim=dict(key="platform_text",name="固定平台文本载荷候选",category="通知",
        action="按平台格式化文本消息",target="机器人平台",business_goal="通知消息编码",
        implementation_mechanism="payload按feishu/ding选择载荷结构，并用换行拼接事件消息",
        evidence_ids=[eid],conditions=["仅固定平台"],limitations=["没有证明任意第三方URL"],
        unresolved_questions=["入口批量事件/网络送达/认证未验证"])
    second={**claim,"key":"webhook_hypothesis","name":"Webhook用途假设",
        "action":"探索JSON投递机制用途","unresolved_questions":["任意地址和认证均未证明"]}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,dict(module_id=mid,
        module_summary="确定性结构测试输出，不是自主发现效果",
        capabilities=[claim,second],candidate_relations=[dict(source_key="platform_text",
            target_key="webhook_hypothesis",relation_type="possible_equivalent",
            rationale="共享JSON文本机制仅可形成待调查线索",evidence_ids=[eid],
            unverified_gaps=["固定平台不能推出任意第三方URL支持"])],
        open_questions=["所有业务语义待人工核查"]))])

# 与真实builder共用全局互斥；此演示直接调用处理器，不冒称模型来自RQ/真实外部服务。
with connection().lock("knowledge-builder-single-worker",timeout=180,blocking_timeout=10):
    with session_factory().begin() as db:
        job=KnowledgeJob(category_id=cid,snapshot_id=sid,job_type="knowledge_build",
            stage="module_planning",dispatch_token="deterministic-example",
            metrics_json={"data_classification":"synthetic_public"})
        db.add(job); db.flush()
        run=Investigation(job_id=job.id,snapshot_id=sid,module_id=mid)
        db.add(run); db.flush()
        jid,rid=job.id,run.id
    run_builder(jid,"deterministic-example",FunctionModel(respond))
    with session_factory()() as db:
        job=db.get(KnowledgeJob,jid); run=db.get(Investigation,rid)
        assert job.status=="succeeded",(job.status,run.error_code)
        ctx=RunContext(category_id=cid,snapshot_id=sid,principal_id="admin",policy="admin_staging")
        evidence=[read_evidence(db,ctx,eid) for eid in run.evidence_ids]
        traces=[{"kind":t.kind,"sequence":t.sequence,"details":t.details_json}
            for t in db.scalars(select(InvestigationTrace).where(
                InvestigationTrace.investigation_id==rid).order_by(InvestigationTrace.sequence))]
        print(json.dumps({"model_mode":"TEST_FUNCTION_MODEL","real_model_evaluation":False,
            "input":"人工p13_assets.source_zip + MANUAL","job_id":jid,"snapshot_id":sid,
            "module_id":mid,"metrics":job.metrics_json,"result":run.output_json,
            "evidence":evidence,"tool_trace":traces},ensure_ascii=False,indent=2))
"""


def main():
    previous = json.loads((ROOT / "real-acceptance.json").read_text())
    cid, sid = previous["category_id"], previous["snapshot_id"]
    env = dotenv_values(".env")
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    client = httpx.Client(base_url="http://127.0.0.1:8001", headers=headers, timeout=10)

    def tool(name, **kwargs):
        r = client.post(
            f"/api/v1/retrieval/{cid}", params={"snapshot_id": sid}, json={"tool": name, **kwargs}
        )
        r.raise_for_status()
        return r.json()

    mid = next(i["module_id"] for i in tool("get_project_map")["results"] if i["name"] == "notify")
    symbol = tool("search_symbols", query="payload")["results"][0]
    args = [
        cid,
        sid,
        mid,
        symbol["file_id"],
        symbol["sha256"],
        str(symbol["start_line"]),
        str(symbol["end_line"]),
    ]
    command = ["docker", "compose", "exec", "-T", "builder", "python", "-c", REMOTE, *args]
    latencies = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(subprocess.run, command, capture_output=True, text=True, check=True)
        while not future.done():
            start = perf_counter()
            tool("search_symbols", query="payload")
            latencies.append((perf_counter() - start) * 1000)
        result = future.result()
    data = json.loads(result.stdout)
    data["retrieval_during_function_model_ms"] = latencies
    (ROOT / "deterministic-example.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
