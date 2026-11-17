"""从P1.4首次发布失败检查点继续；保留历史草稿、审核和失败轨迹。"""

import json
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.ingestion.text import digest
from app.knowledge.evidence_registry import canonical
from app.knowledge.quality import fields

ROOT = Path("reports/p14/round1")
DEST = ROOT / "real-api-postgres-success.json"


def main():
    # 文件存在时必须先人工核对已执行步骤，不能盲目重放副作用。
    with DEST.open("x") as output:
        output.write("{}")
    old = json.loads(Path("reports/p14/real-review-publication.json").read_text())
    original = json.loads(Path("reports/p13-autonomous/real-compact-final.json").read_text())
    baseline = json.loads((ROOT / "diagnosis-before.json").read_text())
    cid, sid = old["category_id"], old["snapshot_id"]
    env = dotenv_values(".env")
    assert env.get("BUILDER_ALLOW_ENTERPRISE", "").lower() != "true"
    clients = {
        role: httpx.Client(
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer " + env[key]},
            timeout=30,
        )
        for role, key in [("admin", "ADMIN_TOKEN"), ("read", "READ_TOKEN")]
    }
    report = {
        "baseline_commit": "9eb697fb48daaaa155b6353a12d00fce27fcfa2d",
        "category_id": cid,
        "snapshot_id": sid,
        "steps": [],
        "model_calls": 0,
        "uploaded_source_executed": False,
        "review_basis": "开发方对既有人工公开源码逐断言复核；不是独立人工用户签字",
        "historical_failure_preserved": True,
    }

    def save():
        DEST.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def call(label, method, path, body=None, expected=200, role="admin"):
        response = (
            clients[role].request(method, path, json=body)
            if body is not None
            else clients[role].request(method, path)
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
                "correlation_id": response.headers.get("x-correlation-id"),
            }
        )
        save()
        assert response.status_code == expected, (label, response.status_code, data)
        return data

    diagnostic_code = Path("scripts/p14_r1_diagnose.py").read_text()
    db_code = """
import json,sys
from sqlalchemy import select
from app.db.models import Category,Snapshot
from app.db.review_models import Publication,PublicationSwitch,ClaimRevision
from app.db.session import session_factory
from app.ingestion.text import digest
from app.knowledge.evidence_registry import canonical
with session_factory()() as db:
 c=db.get(Category,sys.argv[1])
 publications=list(db.scalars(select(Publication).where(Publication.category_id==c.id).order_by(Publication.created_at)))
 result={'active_publication_id':c.active_publication_id,'active_snapshot_id':c.active_snapshot_id,
 'snapshots':[{'id':s.id,'status':s.status} for s in db.scalars(select(Snapshot).where(Snapshot.category_id==c.id))],
 'publications':[{'id':p.id,'snapshot_id':p.snapshot_id,'hash':p.payload_hash,'hash_verified':p.payload_hash==digest(canonical(p.payload_json).encode()),'payload':p.payload_json} for p in publications],
 'switches':[{'id':s.id,'action':s.action,'publication_id':s.publication_id,'previous':s.previous_publication_id} for s in db.scalars(select(PublicationSwitch).where(PublicationSwitch.category_id==c.id).order_by(PublicationSwitch.created_at,PublicationSwitch.id))],
 'revisions':[{'id':r.id,'claim_id':r.claim_id,'number':r.number,'decision':r.decision} for r in db.scalars(select(ClaimRevision).where(ClaimRevision.snapshot_id==sys.argv[2]))],
 'historical_rows':fingerprints(db)}
 print(json.dumps(result,ensure_ascii=False))
"""
    # 只载入本地诊断工具的函数，不执行其发布复现入口。
    prefix = (
        "exec("
        + repr(diagnostic_code)
        + ", globals_for_diagnostic := {'__name__':'diagnostic_library'}); fingerprints=globals_for_diagnostic['fingerprints'];\n"
    )

    def state(label):
        data = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    prefix + db_code,
                    cid,
                    sid,
                ],
                text=True,
            )
        )
        historical = data.pop("historical_rows")
        unchanged = all(
            historical.get(table, {}).get(key) == value
            for table, rows in baseline["historical_rows_before"].items()
            for key, value in rows.items()
        )
        assert unchanged, "历史业务记录发生改变"
        data["all_baseline_rows_unchanged"] = unchanged
        data["historical_row_counts"] = {k: len(v) for k, v in historical.items()}
        report.setdefault("database_states", {})[label] = data
        save()
        return data

    initial = state("before")
    assert initial["active_publication_id"] is None and not initial["publications"]
    failed = next(s for s in old["steps"] if s["label"] == "首次实际原子发布")
    preview_revision = failed["request"]["revision_ids"][0]
    assert any(
        r["id"] == preview_revision and r["decision"] == "accept" for r in initial["revisions"]
    )
    evidence = {e["source_ref"]["path"]: e for e in original["evidence"]}
    claims = {}
    for candidate in original["candidates"]:
        path = next(
            e["source_ref"]["path"]
            for e in original["evidence"]
            if e["id"] in candidate["draft"]["evidence_ids"]
        )
        claims[path] = candidate["id"]
        view = call("核对原稿与当前修订", "GET", "/api/v1/admin/capabilities/" + candidate["id"])
        assert view["original_model_output"] == candidate["draft"]
    for e in evidence.values():
        read = call(
            "重新回读既有真实证据",
            "GET",
            f"/api/v1/evidence/{e['id']}?category_id={cid}&snapshot_id={sid}",
        )
        assert read["quote"] == e["quote"]

    p1 = call("修复后首次发布：复用既有接受修订", "POST", failed["path"], failed["request"])
    current1 = call(
        "首次发布普通用户读取", "GET", f"/api/v1/capabilities?category_id={cid}", role="read"
    )
    state1 = state("after_first")
    assert [state1["active_publication_id"], state1["active_snapshot_id"]] == [
        p1["publication_id"],
        sid,
    ]
    assert len(current1["capabilities"]) == 1
    call(
        "普通用户可读当前证据",
        "GET",
        f"/api/v1/evidence/{evidence['core/i_preview.py']['id']}?category_id={cid}",
        role="read",
    )
    call(
        "普通用户不能指定后台快照",
        "GET",
        f"/api/v1/evidence/{evidence['core/i_preview.py']['id']}?category_id={cid}&snapshot_id={sid}",
        expected=403,
        role="read",
    )

    def review_body(content, path, revision):
        rationales = {
            "name": "名称仅概括本函数分支，不作产品支持承诺",
            "action": "逐行核对条件分支、返回及跳过动作",
            "target": "从函数参数与循环变量确认操作对象",
            "business_goal": "仅表述返回结果的局部用途，不推断用户场景",
            "implementation_mechanism": "仅确认已回读代码中的直接委托及条件，不推断被调用底层保证",
        }
        return {
            "action": "accept",
            "expected_revision": revision,
            "declaration_type": "technical_mechanism",
            "content": content,
            "assertions": [
                {
                    "field": k,
                    "text": v,
                    "evidence_ids": [evidence[path]["id"]],
                    "semantic_decision": "supported",
                    "rationale": rationales.get(k, "按本函数访问与未实现分支保守限定边界"),
                }
                for k, v in fields(content).items()
            ],
            "human_confirmed": True,
            "comment": "开发方逐断言复核；仅静态局部机制，不是独立用户签字",
        }

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
    entry = call(
        "新增接受修订：入口局部分支",
        "POST",
        f"/api/v1/capabilities/{claims['api/endpoint.py']}/review",
        review_body(entry_content, "api/endpoint.py", 1),
    )
    hidden = call(
        "新增接受修订：不承诺底层存储的筛选机制",
        "POST",
        f"/api/v1/capabilities/{claims['core/h_engine.py']}/review",
        review_body(hidden_content, "core/h_engine.py", 0),
    )
    relation = call(
        "确认分发到筛选的条件依赖",
        "POST",
        "/api/v1/capability-relations/review",
        {
            "source_revision_id": entry["id"],
            "target_revision_id": hidden["id"],
            "relation_type": "depends_on",
            "decision": "accept",
            "evidence_ids": [evidence["api/endpoint.py"]["id"], evidence["core/h_engine.py"]["id"]],
            "rationale": "commit分支直接返回process_batch调用；仅限该模式",
            "differences": "入口负责分发，筛选函数负责条件与接受列表，非同义能力",
            "input_contract": "commit传events/store/settings，不传mode",
            "output_contract": "返回process_batch返回值",
            "conditions_and_errors": "仅commit路径依赖，内部异常直接传播；其他mode可能报ValueError",
            "human_confirmed": True,
        },
    )
    assert (
        call(
            "审核新增不改变已冻结清单",
            "GET",
            f"/api/v1/capabilities?category_id={cid}",
            role="read",
        )
        == current1
    )
    p2 = call(
        "第二次原子发布",
        "POST",
        failed["path"],
        {
            "revision_ids": [preview_revision, entry["id"], hidden["id"]],
            "relation_ids": [relation["id"]],
            "expected_active_publication_id": p1["publication_id"],
            "acknowledge_coverage_gaps": True,
            "comment": "发布三项窄范围静态机制，显式保留覆盖未知",
        },
    )
    current2 = call(
        "第二次发布普通用户读取", "GET", f"/api/v1/capabilities?category_id={cid}", role="read"
    )
    state2 = state("after_second")
    assert [state2["active_publication_id"], state2["active_snapshot_id"]] == [
        p2["publication_id"],
        sid,
    ]
    assert len(current2["capabilities"]) == 3 and p2["payload_hash"] != p1["payload_hash"]

    call(
        "过期发布失败",
        "POST",
        failed["path"],
        {**failed["request"], "expected_active_publication_id": p1["publication_id"]},
        expected=409,
    )
    assert state("after_stale_failure") == state2
    call(
        "覆盖未确认发布失败",
        "POST",
        failed["path"],
        {
            **failed["request"],
            "expected_active_publication_id": p2["publication_id"],
            "acknowledge_coverage_gaps": False,
        },
        expected=422,
    )
    assert state("after_gate_failure") == state2
    call(
        "普通用户不能发布",
        "POST",
        failed["path"],
        {**failed["request"], "expected_active_publication_id": p2["publication_id"]},
        expected=403,
        role="read",
    )
    assert (
        call(
            "失败后新清单仍完整可读", "GET", f"/api/v1/capabilities?category_id={cid}", role="read"
        )
        == current2
    )

    call(
        "真实回滚首个冻结清单",
        "POST",
        f"/api/v1/categories/{cid}/rollback",
        {
            "publication_id": p1["publication_id"],
            "expected_active_publication_id": p2["publication_id"],
            "comment": "第一轮整改验收回滚，恢复首次冻结清单",
        },
    )
    restored = call(
        "回滚普通用户读取", "GET", f"/api/v1/capabilities?category_id={cid}", role="read"
    )
    final = state("after_rollback")
    assert restored == current1
    assert [final["active_publication_id"], final["active_snapshot_id"]] == [
        p1["publication_id"],
        sid,
    ]
    assert all(p["hash_verified"] for p in final["publications"])
    assert [s["action"] for s in final["switches"]] == ["publish", "publish", "rollback"]
    assert final["publications"] == state2["publications"]
    assert len(final["publications"]) == 2
    report["comparison"] = {
        "first_publication": p1,
        "second_publication": p2,
        "first_visible_claims": [c["id"] for c in current1["capabilities"]],
        "second_visible_claims": [c["id"] for c in current2["capabilities"]],
        "restored_visible_claims": [c["id"] for c in restored["capabilities"]],
        "first_response_hash": digest(canonical(current1).encode()),
        "restored_response_hash": digest(canonical(restored).encode()),
        "exact_response_restored": restored == current1,
        "failed_requests_left_no_publication_or_switch": True,
        "historical_rows_unchanged": True,
    }
    report["passed"] = True
    save()
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
