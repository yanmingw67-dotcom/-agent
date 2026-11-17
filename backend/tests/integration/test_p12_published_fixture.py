"""仅构造已发布数据库状态的隔离测试夹具；本批不实现发布业务接口。"""

from app.db.models import Category, Snapshot
from app.db.session import session_factory
from tests.conftest import ADMIN, READ
from tests.integration.test_p12_index_retrieval import indexed, tool


def test_read_scope_uses_only_current_pointer_in_fixture(client):
    cid, sid = indexed(client, "waf-current")
    other, other_sid = indexed(client, "ndr-current")
    with session_factory().begin() as db:
        db.get(Snapshot, sid).status = "published"
        db.get(Snapshot, other_sid).status = "published"
        db.get(Category, cid).active_snapshot_id = sid
        db.get(Category, other).active_snapshot_id = other_sid
    foreign = client.post(
        f"/api/v1/retrieval/{other}",
        headers=READ,
        json={"tool": "search_symbols", "query": "format_event"},
    ).json()["results"][0]
    response = client.post(
        f"/api/v1/retrieval/{cid}",
        headers=READ,
        json={"tool": "read_source", "symbol_id": foreign["symbol_id"]},
    )
    assert response.status_code == 404
    current = client.post(
        f"/api/v1/retrieval/{cid}",
        headers=READ,
        json={"tool": "search_symbols", "query": "format_event"},
    )
    assert current.status_code == 200 and current.json()["snapshot_id"] == sid
    assert client.post(f"/api/v1/snapshots/{sid}/build", headers=ADMIN).status_code == 409


def test_module_context_is_typed_limited_and_metrics_exist(client, environment):
    cid, sid = indexed(client)
    modules = tool(client, cid, sid, "get_project_map").json()["results"]
    module = next(m for m in modules if m["name"] == "notify")
    context = tool(client, cid, sid, "get_module_context", module_id=module["id"]).json()
    assert context["module_context"]["symbols"]
    assert context["module_context"]["dependencies_status"] == "unresolved"
    object.__setattr__(environment, "retrieval_max_chars", 4)
    context = tool(client, cid, sid, "get_module_context", module_id=module["id"]).json()
    total = sum(len(i["snippet"] or "") for i in context["results"])
    total += sum(len(i["snippet"] or "") for i in context["module_context"]["symbols"])
    assert total <= 4 and context["truncated"]
    metrics = client.get("/api/v1/admin/metrics", headers=ADMIN).json()
    assert metrics["database_queries"]["count"] > 0
    assert metrics["retrieval_requests"]["count"] > 0
