"""仅回滚事务的真实发布诊断；不改变已有数据，不输出SQL参数或异常正文。"""

import hashlib
import json
import sys

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.db import index_models, knowledge_models, review_models  # noqa: F401
from app.db.models import Base, Category
from app.db.session import get_engine, session_factory
from app.knowledge.publisher import publish
from app.knowledge.review_schemas import PublishRequest


def fingerprints(db):
    names = [
        "capability_claims",
        "claim_revisions",
        "review_events",
        "reviewed_relations",
        "knowledge_publications",
        "publication_switches",
        "module_investigations",
        "investigation_traces",
        "evidence",
        "capability_relations",
    ]
    result = {}
    for name in names:
        table = Base.metadata.tables[name]
        rows = db.execute(select(table)).mappings()
        result[name] = {
            str(r["id"]): hashlib.sha256(
                json.dumps(
                    dict(r), sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            for r in rows
        }
    return result


def main():
    request = json.loads(sys.stdin.read())
    sid, cid = request["snapshot_id"], request["category_id"]
    with session_factory()() as db:
        before = fingerprints(db)
        category = db.get(Category, cid)
        pointers = {
            "active_publication_id": category.active_publication_id,
            "active_snapshot_id": category.active_snapshot_id,
        }
    result = {"historical_rows_before": before, "pointers_before": pointers}
    with get_engine().connect() as connection:
        result["constraints"] = {
            name: inspect(connection).get_foreign_keys(name)
            for name in ["knowledge_publications", "publication_switches", "product_categories"]
        }
        result["isolation"] = connection.execute(text("SHOW transaction_isolation")).scalar()
    with session_factory()() as db:
        try:
            row = publish(db, sid, PublishRequest.model_validate(request["body"]))
            db.flush()
            result["diagnosis"] = {"unexpected_success_rolled_back": True, "id": row.id}
        except DBAPIError as exc:
            orig = exc.orig
            diag = getattr(orig, "diag", None)
            result["diagnosis"] = {
                "exception_type": type(exc).__name__,
                "driver_exception_type": type(orig).__name__,
                "sqlstate": getattr(orig, "sqlstate", None),
                "constraint_name": getattr(diag, "constraint_name", None),
                "table_name": getattr(diag, "table_name", None),
                "column_name": getattr(diag, "column_name", None),
                "statement_kind": (exc.statement or "").split(" ", 1)[0],
            }
        finally:
            db.rollback()
    with session_factory()() as db:
        after = fingerprints(db)
        category = db.get(Category, cid)
        result["pointers_after"] = {
            "active_publication_id": category.active_publication_id,
            "active_snapshot_id": category.active_snapshot_id,
        }
    result["all_historical_rows_unchanged"] = before == after
    assert before == after and result["pointers_after"] == pointers
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
