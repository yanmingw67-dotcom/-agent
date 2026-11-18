"""P1.6 文档单源能力在真实 PostgreSQL 上的审核、发布与回滚。

P16_RUN_POSTGRES=1 时运行；只使用随机专用 schema，结束 DROP SCHEMA CASCADE。
不替换审核/发布业务函数，不改 public 业务数据。
"""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db import index_models, knowledge_models, review_models  # noqa: F401
from app.db.index_models import Document, DocumentSegment, Module
from app.db.knowledge_models import CapabilityClaim, Investigation
from app.db.models import Category, KnowledgeJob, Snapshot
from app.db.review_models import Publication
from app.ingestion.text import digest
from app.knowledge import publisher, review
from app.knowledge.evidence_registry import register
from app.knowledge.quality import fields
from app.knowledge.review_schemas import PublishRequest, ReviewRequest, RollbackRequest
from app.knowledge.schemas import EvidenceRequest
from app.retrieval.schemas import RunContext


@unittest.skipUnless(os.getenv("P16_RUN_POSTGRES") == "1", "需显式真实PostgreSQL测试环境")
class P16PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_env = {
            key: os.environ.get(key)
            for key in [
                "DATABASE_URL",
                "SOURCE_ROOT",
                "INGESTION_ROOT",
                "ARCHIVE_ROOT",
                "ADMIN_TOKEN",
                "READ_TOKEN",
            ]
        }
        base = get_settings().database_url
        assert make_url(base).get_backend_name() == "postgresql"
        cls.admin_engine = create_engine(base)
        cls.schema = "p16_test_" + uuid4().hex
        cls.directory = tempfile.TemporaryDirectory(prefix="p16-pg-")
        with cls.admin_engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        url = make_url(base).update_query_dict({"options": f"-csearch_path={cls.schema}"})
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        os.environ["SOURCE_ROOT"] = cls.directory.name
        os.environ["INGESTION_ROOT"] = cls.directory.name
        os.environ["ARCHIVE_ROOT"] = str(Path(cls.directory.name) / "archives")
        os.environ["ADMIN_TOKEN"] = "a" * 48
        os.environ["READ_TOKEN"] = "r" * 48
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        from app.db.session import get_engine

        get_engine.cache_clear()
        cls.engine = create_engine(url)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        from app.db.session import get_engine

        get_engine().dispose()
        get_engine.cache_clear()
        assert cls.schema.startswith("p16_test_") and len(cls.schema) == 41
        with cls.admin_engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin_engine.dispose()
        cls.directory.cleanup()
        for key, value in cls.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    def seed(self, cid=None, text_value="支持策略模板导入"):
        with self.sessions.begin() as db:
            if cid is None:
                category = Category(code="p16-" + uuid4().hex, name="文档单源真实PG测试")
                db.add(category)
                db.flush()
                cid = category.id
            snapshot = Snapshot(
                category_id=cid,
                source_sha256="0" * 64,
                archive_key=uuid4().hex,
                status="extracted",
                index_status="indexed",
                data_classification="synthetic_public",
            )
            db.add(snapshot)
            db.flush()
            sid = snapshot.id
            module = Module(snapshot_id=sid, name="__document__:test", path_globs=[])
            job = KnowledgeJob(category_id=cid, snapshot_id=sid, status="succeeded")
            db.add_all([module, job])
            db.flush()
            run = Investigation(
                job_id=job.id, snapshot_id=sid, module_id=module.id, status="succeeded"
            )
            db.add(run)
            db.flush()
            data = ("合成技术手册：当前版本" + text_value + "。").encode()
            key = uuid4().hex
            path = get_settings().document_store / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            document = Document(
                category_id=cid,
                snapshot_id=sid,
                filename="manual.txt",
                storage_key=key,
                sha256=digest(data),
                size=len(data),
                parse_status="parsed",
                parser_version="test",
            )
            db.add(document)
            db.flush()
            segment = DocumentSegment(
                snapshot_id=sid,
                document_id=document.id,
                locator={"page": 1},
                text=data.decode(),
                text_hash=digest(data),
                sensitive=False,
            )
            db.add(segment)
            db.flush()
            receipt = register(
                db,
                RunContext(
                    category_id=cid,
                    snapshot_id=sid,
                    principal_id="admin",
                    policy="admin_staging",
                ),
                EvidenceRequest(
                    kind="document",
                    segment_id=segment.id,
                    expected_sha256=segment.text_hash,
                    supported_statement=segment.text,
                ),
            )
            content = {
                "name": "策略模板导入",
                "action": "提供策略模板导入",
                "target": "策略模板",
                "business_goal": "由管理员维护并导入模板",
                "implementation_mechanism": "文档声明提供模板导入能力",
                "supported_scope": ["文档所述当前版本"],
                "conditions": [],
                "limitations": ["未记录底层实现机制"],
            }
            claim = CapabilityClaim(
                id="p16-" + uuid4().hex,
                snapshot_id=sid,
                investigation_id=run.id,
                local_key=uuid4().hex,
                name="策略模板导入",
                status="evidence_found",
                draft_json={
                    "name": "策略模板导入",
                    "source_channel": "document",
                    "evidence_ids": [receipt.evidence_id],
                },
                source_channel="document",
                origin_json={"document_id": document.id},
            )
            db.add(claim)
            db.flush()
            row = review.review(
                db,
                claim.id,
                ReviewRequest(
                    action="accept",
                    expected_revision=0,
                    declaration_type="product_function",
                    content=content,
                    assertions=[
                        {
                            "field": key,
                            "text": value,
                            "evidence_ids": [receipt.evidence_id],
                            "semantic_decision": "supported",
                            "rationale": "逐字核对文档声明，仅限明示范围",
                            "proof_basis": "document",
                            "document_scope_confirmed": True,
                        }
                        for key, value in fields(content).items()
                    ],
                    human_confirmed=True,
                    availability_basis="document_declared",
                    product_scope_confirmed=True,
                    comment="真实PG文档单源审核",
                ),
            )
            return cid, sid, claim.id, row.id

    def body(self, revisions, expected=None, comment="真实PG文档发布"):
        return PublishRequest(
            revision_ids=revisions,
            expected_active_publication_id=expected,
            acknowledge_coverage_gaps=True,
            comment=comment,
        )

    def pointers(self, cid):
        with self.sessions() as db:
            category = db.get(Category, cid)
            return category.active_publication_id, category.active_snapshot_id

    def test_document_publish_and_rollback_on_postgres(self):
        cid, first_sid, _, first_revision = self.seed()
        with self.sessions.begin() as db:
            first = publisher.publish(db, first_sid, self.body([first_revision]))
        self.assertEqual(self.pointers(cid), (first.id, first_sid))
        with self.sessions() as db:
            payload = db.get(Publication, first.id).payload_json
            item = payload["capabilities"][0]
            self.assertEqual(item["declaration_type"], "product_function")
            self.assertEqual(item["assertions"][0]["document_scope_confirmed"], True)
            self.assertIsNotNone(
                db.scalar(select(Document.id).where(Document.snapshot_id == first_sid))
            )

        _, second_sid, _, second_revision = self.seed(cid, text_value="支持策略模板版本升级")
        with self.sessions.begin() as db:
            second = publisher.publish(
                db, second_sid, self.body([second_revision], first.id, "真实PG文档更新发布")
            )
        self.assertEqual(self.pointers(cid), (second.id, second_sid))
        with self.sessions.begin() as db:
            restored = publisher.rollback(
                db,
                cid,
                RollbackRequest(
                    publication_id=first.id,
                    expected_active_publication_id=second.id,
                    comment="真实PG文档回滚复原",
                ),
            )
            self.assertEqual(restored.payload_json, first.payload_json)
            self.assertEqual(restored.payload_hash, first.payload_hash)
        self.assertEqual(self.pointers(cid), (first.id, first_sid))

    def test_staging_derivation_update_conflict_publish_and_rollback_on_postgres(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import event

        from app.db.session import get_engine
        from app.main import create_app
        from tests import p16_assets
        from tests.conftest import ADMIN
        from tests.integration.test_p16_discovery import (
            discover,
            document_review,
            draft_claim,
            index_document,
        )
        from tests.integration.test_p16_staging import build, derive, document, setup

        engine = get_engine()

        @event.listens_for(engine, "connect")
        def _search_path(dbapi_connection, _record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{self.schema}"')

        with TestClient(create_app()) as client:
            cid, sid = setup(client)
            index_document(
                document(
                    client,
                    cid,
                    sid,
                    "manual.docx",
                    p16_assets.docx_manual(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            )
            index_document(
                document(
                    client,
                    cid,
                    sid,
                    "matrix.xlsx",
                    p16_assets.xlsx_capabilities(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            )
            discover(client, sid)
            with self.sessions() as db:
                conflicts = list(
                    db.scalars(
                        select(review_models.ConflictFinding).where(
                            review_models.ConflictFinding.snapshot_id == sid
                        )
                    )
                )
            self.assertTrue(
                any(item.conflict_type == "support_exclusion_conflict" for item in conflicts)
            )
            conflicted = draft_claim(sid, "规则同步")
            blocked = client.post(
                f"/api/v1/capabilities/{conflicted.id}/review",
                headers=ADMIN,
                json=document_review(conflicted, name="规则同步"),
            )
            self.assertEqual(blocked.status_code, 422, blocked.text)
            self.assertEqual(blocked.json()["error_code"], "UNRESOLVED_CONFLICT")

            claim = draft_claim(sid, "多租户隔离")
            reviewed = client.post(
                f"/api/v1/capabilities/{claim.id}/review",
                headers=ADMIN,
                json=document_review(claim, name="多租户隔离"),
            )
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            first = client.post(
                f"/api/v1/snapshots/{sid}/publish",
                headers=ADMIN,
                json={
                    "revision_ids": [reviewed.json()["id"]],
                    "expected_active_publication_id": None,
                    "acknowledge_coverage_gaps": True,
                    "comment": "真实PG发布文档单源能力",
                },
            )
            self.assertEqual(first.status_code, 200, first.text)

            derived = derive(client, cid)
            new_sid = derived["snapshot_id"]
            self.assertEqual(derived["derived_from_snapshot_id"], sid)
            build(client, new_sid)
            with self.sessions() as db:
                new_docx = db.scalar(
                    select(Document).where(
                        Document.snapshot_id == new_sid, Document.filename == "manual.docx"
                    )
                )
                old_docx = db.scalar(
                    select(Document).where(
                        Document.snapshot_id == sid, Document.filename == "manual.docx"
                    )
                )
                old_key, old_sha = old_docx.storage_key, old_docx.sha256
            old_bytes = (get_settings().document_store / old_key).read_bytes()
            replaced = client.post(
                f"/api/v1/snapshots/{new_sid}/documents/{new_docx.id}/replace",
                headers=ADMIN,
                files={
                    "file": (
                        "manual.docx",
                        p16_assets.docx_manual(revision="v2"),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            self.assertEqual(replaced.status_code, 202, replaced.text)
            with self.sessions.begin() as db:
                db.get(KnowledgeJob, replaced.json()["job_id"]).dispatch_token = "p16-pg-replace"
            from app.ingestion.indexer import run_index

            run_index(replaced.json()["job_id"], "p16-pg-replace")
            with self.sessions() as db:
                self.assertEqual(db.get(Document, old_docx.id).sha256, old_sha)
            self.assertEqual((get_settings().document_store / old_key).read_bytes(), old_bytes)

            discover(client, new_sid)
            new_claim = draft_claim(new_sid, "当前版本支持策略模板导入，模板由管理员维护。")
            self.assertIsNotNone(new_claim)
            new_reviewed = client.post(
                f"/api/v1/capabilities/{new_claim.id}/review",
                headers=ADMIN,
                json=document_review(
                    new_claim, name="当前版本支持策略模板导入，模板由管理员维护。"
                ),
            )
            self.assertEqual(new_reviewed.status_code, 200, new_reviewed.text)
            second = client.post(
                f"/api/v1/snapshots/{new_sid}/publish",
                headers=ADMIN,
                json={
                    "revision_ids": [new_reviewed.json()["id"]],
                    "expected_active_publication_id": first.json()["publication_id"],
                    "acknowledge_coverage_gaps": True,
                    "comment": "真实PG发布暂存文档更新",
                },
            )
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(self.pointers(cid), (second.json()["publication_id"], new_sid))
            rolled = client.post(
                f"/api/v1/categories/{cid}/rollback",
                headers=ADMIN,
                json={
                    "publication_id": first.json()["publication_id"],
                    "expected_active_publication_id": second.json()["publication_id"],
                    "comment": "真实PG回滚暂存更新",
                },
            )
            self.assertEqual(rolled.status_code, 200, rolled.text)
            self.assertEqual(self.pointers(cid), (first.json()["publication_id"], sid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
