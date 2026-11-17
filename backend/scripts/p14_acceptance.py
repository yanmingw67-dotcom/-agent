"""实际P1.3模型草稿的开发方逐断言审核案例；不生成模型答案，不自动批准。"""

import json
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path("reports/p14")
DEST = ROOT / "real-review-publication.json"


def main():
    assert not DEST.exists(), "不可覆盖验收轨迹"
    original = json.loads(Path("reports/p13-autonomous/real-compact-final.json").read_text())
    sid, cid = original["snapshot_id"], original["category_id"]
    env = dotenv_values(".env")
    assert env.get("BUILDER_ALLOW_ENTERPRISE", "").lower() != "true"
    admin = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]},
        timeout=30,
    )
    reader = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": "Bearer " + env["READ_TOKEN"]},
        timeout=30,
    )
    report = {
        "mode": "REAL_EXISTING_MODEL_DRAFTS_DEVELOPER_REVIEW_REAL_API_POSTGRES",
        "model_requests_this_phase": 0,
        "model_tokens_this_phase": 0,
        "cost_this_phase_usd": None,
        "cost_basis": "not_called_no_new_model_bill",
        "source_model_job_id": original["job_id"],
        "snapshot_id": sid,
        "category_id": cid,
        "decisions_are_developer_review_not_independent_human_signoff": True,
        "steps": [],
    }
    started = time.monotonic()

    def call(label, method, path, body=None, expected=200, read=False):
        client = reader if read else admin
        response = (
            client.request(method, path, json=body)
            if body is not None
            else client.request(method, path)
        )
        data = response.json()
        report["steps"].append(
            {
                "label": label,
                "method": method,
                "path": path,
                "request": body,
                "status": response.status_code,
                "response": data,
            }
        )
        DEST.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        assert response.status_code == expected, (label, response.status_code, data)
        return data

    evidence = {e["source_ref"]["path"]: e for e in original["evidence"]}
    claims = {}
    for c in original["candidates"]:
        path = next(
            e["source_ref"]["path"]
            for e in original["evidence"]
            if e["id"] in c["draft"]["evidence_ids"]
        )
        claims[path] = c["id"]
        view = call("读取真实原始模型草稿", "GET", "/api/v1/admin/capabilities/" + c["id"])
        assert view["latest_revision"] is None, "拒绝覆盖已存在人工审核"
    for _path, record in evidence.items():
        call(
            "实际回读源码原文及哈希",
            "GET",
            f"/api/v1/evidence/{record['id']}?category_id={cid}&snapshot_id={sid}",
        )
    entry = claims["api/endpoint.py"]
    call(
        "调查动作退回",
        "POST",
        f"/api/v1/capabilities/{entry}/review",
        {
            "action": "return",
            "expected_revision": 0,
            "declaration_type": "investigation_action",
            "comment": "原始name/action是展开/检索计划，不能作为产品能力。保留原稿并退回。",
        },
    )
    hidden = claims["core/h_engine.py"]

    def body(path, content, revision=0, action="accept"):
        from app.knowledge.quality import fields

        eid = evidence[path]["id"]
        return {
            "action": action,
            "expected_revision": revision,
            "declaration_type": "technical_mechanism",
            "content": content,
            "assertions": [
                {
                    "field": k,
                    "text": v,
                    "evidence_ids": [eid],
                    "semantic_decision": "supported",
                    "rationale": "开发方按实际回读逐项核对，只确认函数局部语义，不扩展产品保证",
                }
                for k, v in fields(content).items()
            ],
            "comment": "开发方源码复核：局部机制，不是正式产品支持保证",
            "human_confirmed": True,
        }

    preview_content = {
        "name": "单次调用按标识保序首项机制",
        "action": "保留每个标识首次出现的事件",
        "target": "事件列表",
        "business_goal": "生成预览列表",
        "implementation_mechanism": "局部seen集合记录已出现标识；首次遇到时追加事件到result，最后返回result",
        "supported_scope": ["单次函数调用内"],
        "conditions": ["事件含id且id可哈希"],
        "limitations": ["不同租户的相同id也会合并", "不保证跨调用效果"],
    }
    risky = dict(preview_content, name="持久化幂等去重", action="持久占位保证跨请求幂等")
    call(
        "无底层实现的持久断言阻断",
        "POST",
        f"/api/v1/capabilities/{hidden}/validate",
        body("core/h_engine.py", risky),
    )
    call(
        "不得人工确认绕过高风险缺口",
        "POST",
        f"/api/v1/capabilities/{hidden}/review",
        body("core/h_engine.py", risky),
        expected=422,
    )
    preview = claims["core/i_preview.py"]
    b = body("core/i_preview.py", preview_content, action="modify")
    modified = call("窄化修订不自动接受", "POST", f"/api/v1/capabilities/{preview}/review", b)
    call(
        "未接受修订不能发布",
        "POST",
        f"/api/v1/snapshots/{sid}/publish",
        {
            "revision_ids": [modified["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "故意尝试未审核发布，必须失败",
        },
        expected=422,
    )
    accepted_preview = call(
        "逐断言显式接受局部预览机制",
        "POST",
        f"/api/v1/capabilities/{preview}/review",
        body("core/i_preview.py", preview_content, revision=1),
    )

    def pub(label, revisions, expected, relations=None):
        return call(
            label,
            "POST",
            f"/api/v1/snapshots/{sid}/publish",
            {
                "revision_ids": revisions,
                "relation_ids": relations or [],
                "expected_active_publication_id": expected,
                "acknowledge_coverage_gaps": True,
                "comment": "人工确认窄范围机制，调查未完成及不确定断言继续保留",
            },
        )

    p1 = pub("首次实际原子发布", [accepted_preview["id"]], None)
    current1 = call(
        "只读用户当前能力清单", "GET", f"/api/v1/capabilities?category_id={cid}", read=True
    )
    entry_content = {
        "name": "模式选择委托机制",
        "action": "按mode选择被调函数或报错",
        "target": "事件处理调用",
        "business_goal": "区分预览与提交分支",
        "implementation_mechanism": "preview分支仅传events；commit分支传events、store、settings；其他模式抛ValueError",
        "supported_scope": ["handle_events函数局部条件分支"],
        "conditions": ["mode精确匹配preview或commit"],
        "limitations": ["被调函数内部行为另行审核", "不证明网络接口注册"],
    }
    hidden_content = {
        "name": "条件检查与接受列表机制",
        "action": "按配置检查租户并按调用结果筛选事件",
        "target": "事件批次",
        "business_goal": "构建接受结果列表",
        "implementation_mechanism": "tenant_required启用且无tenant则报错；deduplicate启用时调用reserve_once，假值跳过，否则追加事件",
        "supported_scope": ["process_batch函数局部"],
        "conditions": ["settings包含两个开关键", "事件输入满足字典访问前提"],
        "limitations": ["不保证存储持久性或原子性", "未提供批次回滚逻辑"],
    }
    accepted_entry = call(
        "调查动作修正为实际分支机制并审核",
        "POST",
        f"/api/v1/capabilities/{entry}/review",
        body("api/endpoint.py", entry_content, revision=1),
    )
    accepted_hidden = call(
        "去掉越界承诺后接受局部调用机制",
        "POST",
        f"/api/v1/capabilities/{hidden}/review",
        body("core/h_engine.py", hidden_content),
    )
    relation_body = {
        "source_revision_id": accepted_entry["id"],
        "target_revision_id": accepted_hidden["id"],
        "relation_type": "depends_on",
        "decision": "accept",
        "evidence_ids": [evidence["api/endpoint.py"]["id"], evidence["core/h_engine.py"]["id"]],
        "rationale": "入口commit分支直接调用process_batch，为条件性依赖，不是严格等效",
        "differences": "分发与具体筛选属于不同层次，不能合并",
        "input_contract": "入口含mode，被调用方接收events/store/settings",
        "output_contract": "commit返回被调方结果，preview走另一函数",
        "conditions_and_errors": "仅commit依赖该实现；其他mode可报ValueError",
        "human_confirmed": True,
    }
    relation = call(
        "核验条件性depends_on关系", "POST", "/api/v1/capability-relations/review", relation_body
    )
    trap = {
        **relation_body,
        "source_revision_id": accepted_hidden["id"],
        "target_revision_id": accepted_preview["id"],
        "relation_type": "possible_equivalent",
        "decision": "reject",
        "evidence_ids": [evidence["core/h_engine.py"]["id"], evidence["core/i_preview.py"]["id"]],
        "rationale": "局部集合去重不等于委托存储筛选；存储底层未知，不应认定一般等效",
    }
    call("拒绝易误判等效关系", "POST", "/api/v1/capability-relations/review", trap)
    p2 = pub(
        "实际审核清单更新原子发布",
        [accepted_preview["id"], accepted_entry["id"], accepted_hidden["id"]],
        p1["publication_id"],
        [relation["id"]],
    )
    current2 = call(
        "只读用户只见完整新清单", "GET", f"/api/v1/capabilities?category_id={cid}", read=True
    )
    assert len(current2["capabilities"]) == 3
    call(
        "过期指针发布失败不覆盖",
        "POST",
        f"/api/v1/snapshots/{sid}/publish",
        {
            "revision_ids": [accepted_preview["id"]],
            "expected_active_publication_id": p1["publication_id"],
            "acknowledge_coverage_gaps": True,
            "comment": "过期并发请求应失败",
        },
        expected=409,
    )
    call(
        "实际回滚至首个冻结清单",
        "POST",
        f"/api/v1/categories/{cid}/rollback",
        {
            "publication_id": p1["publication_id"],
            "expected_active_publication_id": p2["publication_id"],
            "comment": "验收回滚，恢复首个已审核窄范围知识清单",
        },
    )
    restored = call("回滚后读取一致", "GET", f"/api/v1/capabilities?category_id={cid}", read=True)
    assert restored == current1
    report["switches"] = call("发布回滚审计记录", "GET", f"/api/v1/categories/{cid}/publications")
    report["coverage"] = call("未调查入口与断言保留", "GET", f"/api/v1/snapshots/{sid}/coverage")
    for _path, claim in claims.items():
        view = call("原始模型记录未改写", "GET", "/api/v1/admin/capabilities/" + claim)
        expected = next(c["draft"] for c in original["candidates"] if c["id"] == claim)
        assert view["original_model_output"] == expected
    code = """
import json,sys
from sqlalchemy import select
from app.db.session import session_factory
from app.db.models import Category
from app.db.review_models import ClaimRevision,ReviewEvent,Publication,PublicationSwitch,ReviewedRelation
with session_factory()() as db:
 c=db.get(Category,sys.argv[1])
 print(json.dumps({'active_snapshot_id':c.active_snapshot_id,'active_publication_id':c.active_publication_id,
 'revisions':[{'id':r.id,'claim_id':r.claim_id,'number':r.number,'decision':r.decision,'type':r.declaration_type} for r in db.scalars(select(ClaimRevision).where(ClaimRevision.snapshot_id==c.active_snapshot_id))],
 'publications':[{'id':p.id,'hash':p.payload_hash,'count':len(p.payload_json['capabilities'])} for p in db.scalars(select(Publication).where(Publication.category_id==c.id))],
 'switches':[{'action':s.action,'publication_id':s.publication_id,'previous':s.previous_publication_id} for s in db.scalars(select(PublicationSwitch).where(PublicationSwitch.category_id==c.id))]},ensure_ascii=False))
"""
    report["database"] = json.loads(
        subprocess.check_output(
            ["docker", "compose", "exec", "-T", "api", "python", "-c", code, cid], text=True
        )
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["passed"] = True
    DEST.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "artifact": str(DEST),
                "passed": True,
                "elapsed_seconds": report["elapsed_seconds"],
                "publications": report["database"]["publications"],
                "switches": report["database"]["switches"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
