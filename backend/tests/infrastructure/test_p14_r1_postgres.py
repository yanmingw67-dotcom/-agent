"""真实PostgreSQL触发器、双Session并发及事务故障测试。

P14_RUN_POSTGRES=1时运行。仅在随机专用schema中建表与清理；不改public业务数据。
使用真实迁移0001～0007、真实证据门槛；不替换发布/合并业务函数。
"""

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.logging import database_diagnostic
from app.core.security import AppError
from app.db import index_models, knowledge_models, review_models  # noqa: F401
from app.db.index_models import Module, SourceFile
from app.db.knowledge_models import CapabilityClaim, Investigation
from app.db.models import Category, KnowledgeJob, Snapshot
from app.db.review_models import ClaimRevision, Publication, PublicationSwitch
from app.ingestion.text import digest
from app.knowledge import publisher, review
from app.knowledge.evidence_registry import register
from app.knowledge.quality import context, fields, latest
from app.knowledge.review_schemas import (
    MergeRequest,
    PublishRequest,
    ReviewRequest,
    RollbackRequest,
)
from app.knowledge.schemas import EvidenceRequest


@unittest.skipUnless(os.getenv("P14_RUN_POSTGRES") == "1", "需显式真实PostgreSQL测试环境")
class PostgreSQLRemediationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_env = {k: os.environ.get(k) for k in ["DATABASE_URL", "SOURCE_ROOT"]}
        base = get_settings().database_url
        assert make_url(base).get_backend_name() == "postgresql"
        cls.admin_engine = create_engine(base)
        cls.schema = "p14_r1_test_" + uuid4().hex
        cls.directory = tempfile.TemporaryDirectory(prefix="p14-r1-source-")
        with cls.admin_engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        url = make_url(base).update_query_dict(
            {"options": f"-csearch_path={cls.schema} -cstatement_timeout=10000 -clock_timeout=8000"}
        )
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        os.environ["SOURCE_ROOT"] = cls.directory.name
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        cls.engine = create_engine(url)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)
        with cls.engine.begin() as db:
            # 仅测试schema的故障注入；分别覆盖flush失败和提交失败。
            db.execute(
                text("""
                CREATE FUNCTION p14_test_switch_failure() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.comment IN ('inject-immediate', 'inject-deferred') THEN
                        RAISE EXCEPTION 'controlled publication failure'
                        USING ERRCODE='23514', CONSTRAINT='p14_injected_switch_failure';
                    END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER p14_test_immediate BEFORE INSERT ON publication_switches
                FOR EACH ROW WHEN (NEW.comment = 'inject-immediate')
                EXECUTE FUNCTION p14_test_switch_failure();
                CREATE CONSTRAINT TRIGGER p14_test_deferred AFTER INSERT ON publication_switches
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                WHEN (NEW.comment = 'inject-deferred') EXECUTE FUNCTION p14_test_switch_failure();
            """)
            )

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        # 仅删除本次随机创建的测试schema，绝不删除public或重置数据库。
        assert cls.schema.startswith("p14_r1_test_") and len(cls.schema) == 44
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

    def seed(self, cid=None):
        with self.sessions.begin() as db:
            if cid is None:
                category = Category(code="r1-" + uuid4().hex, name="隔离并发测试品类")
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
            module = Module(snapshot_id=sid, name="test", path_globs=["*.py"])
            job = KnowledgeJob(category_id=cid, snapshot_id=sid, status="succeeded")
            db.add_all([module, job])
            db.flush()
            run = Investigation(
                job_id=job.id, snapshot_id=sid, module_id=module.id, status="succeeded"
            )
            db.add(run)
            code = b"def first(items):\n    return items[0]\n"
            path = Path(self.directory.name) / sid / "source" / "mechanism.py"
            path.parent.mkdir(parents=True)
            path.write_bytes(code)
            source = SourceFile(
                snapshot_id=sid,
                path="mechanism.py",
                sha256=digest(code),
                size=len(code),
                language="python",
                role="source",
                encoding="utf-8",
                line_count=2,
                parse_status="parsed",
                parser_version="test",
                module_id=module.id,
            )
            db.add(source)
            db.flush()
            receipt = register(
                db,
                context(db, sid),
                EvidenceRequest(
                    kind="source",
                    file_id=source.id,
                    start_line=1,
                    end_line=2,
                    expected_sha256=source.sha256,
                    supported_statement="隔离自动化测试的首项返回机制",
                ),
            )
            content = {
                "name": "首项返回",
                "action": "返回首项",
                "target": "输入序列",
                "business_goal": "获取首项",
                "implementation_mechanism": "返回索引0处元素",
                "supported_scope": ["函数内部"],
                "conditions": ["输入支持索引且非空"],
                "limitations": ["未提供空输入处理"],
            }
            revisions, claims = [], []
            for i in range(2):
                claim = CapabilityClaim(
                    id="r1-" + uuid4().hex,
                    snapshot_id=sid,
                    investigation_id=run.id,
                    local_key=str(i),
                    name="测试候选",
                    status="candidate",
                    draft_json={"name": "测试候选"},
                )
                db.add(claim)
                db.flush()
                row = review.review(
                    db,
                    claim.id,
                    ReviewRequest(
                        action="accept",
                        expected_revision=0,
                        declaration_type="technical_mechanism",
                        content=content,
                        assertions=[
                            {
                                "field": k,
                                "text": v,
                                "evidence_ids": [receipt.evidence_id],
                                "semantic_decision": "supported",
                                "rationale": "仅隔离自动化测试固定语义",
                            }
                            for k, v in fields(content).items()
                        ],
                        human_confirmed=True,
                        comment="隔离测试，不是业务审核签字",
                    ),
                )
                claims.append(claim.id)
                revisions.append(row.id)
        return cid, sid, claims, revisions

    def publish_body(self, revisions, expected=None, comment="隔离测试发布"):
        return PublishRequest(
            revision_ids=revisions,
            expected_active_publication_id=expected,
            acknowledge_coverage_gaps=True,
            comment=comment,
        )

    def state(self, cid):
        with self.sessions() as db:
            c = db.get(Category, cid)
            return {
                "pointers": [c.active_publication_id, c.active_snapshot_id],
                "snapshots": dict(
                    db.execute(
                        select(Snapshot.id, Snapshot.status).where(Snapshot.category_id == cid)
                    ).all()
                ),
                "publications": dict(
                    db.execute(
                        select(Publication.id, Publication.payload_hash).where(
                            Publication.category_id == cid
                        )
                    ).all()
                ),
                "switches": db.scalar(
                    select(func.count())
                    .select_from(PublicationSwitch)
                    .where(PublicationSwitch.category_id == cid)
                ),
            }

    def contended(self, writer, action):
        """事件同步证明第二连接已被第一连接阻塞，再提交第一事务。"""
        attempting = threading.Event()
        finished = threading.Event()
        pid = []

        def run():
            with self.sessions() as reader:
                pid.append(reader.scalar(text("SELECT pg_backend_pid()")))

                def before(conn, cursor, statement, parameters, ctx, many):
                    if "FOR UPDATE" in statement:
                        attempting.set()

                connection = reader.connection()
                event.listen(connection, "before_cursor_execute", before)
                try:
                    return action(reader)
                finally:
                    reader.rollback()
                    finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            try:
                self.assertTrue(attempting.wait(3), "第二Session未到达锁")
                self.assertFalse(finished.wait(0.1), "持锁时第二Session不应完成")
                with self.engine.connect() as observer:
                    blockers = observer.execute(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pid[0]}
                    ).scalar()
                self.assertIn(writer.scalar(text("SELECT pg_backend_pid()")), blockers)
                writer.commit()
                return future.result(timeout=5)
            finally:
                writer.rollback()

    def merge_race(self, changed_side):
        _, sid, claims, revisions = self.seed()
        with self.sessions() as writer:
            review.review(
                writer,
                claims[changed_side],
                ReviewRequest(action="return", expected_revision=1, comment="并发审核事务退回"),
            )

            def merge(reader):
                try:
                    review.merge(
                        reader,
                        MergeRequest(
                            source_claim_id=claims[0],
                            target_claim_id=claims[1],
                            source_revision=1,
                            target_revision=1,
                            relation_type="same_as",
                            human_confirmed=True,
                            comment="不能使用旧审核合并",
                        ),
                    )
                except AppError as error:
                    return error.error_code
                self.fail("陈旧修订被合并")

            self.assertEqual(self.contended(writer, merge), "MERGE_REVISION_CONFLICT")
        with self.sessions() as db:
            self.assertEqual(latest(db, claims[changed_side]).number, 2)
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(ClaimRevision)
                    .where(ClaimRevision.snapshot_id == sid, ClaimRevision.decision == "merged")
                ),
                0,
            )

    def test_concurrent_source_review_prevents_merge(self):
        self.merge_race(0)

    def test_concurrent_target_review_prevents_merge(self):
        self.merge_race(1)

    def test_identity_map_refresh_rejects_changed_snapshot(self):
        cid, sid, _, _ = self.seed()
        with self.sessions() as stale:
            snapshot = stale.get(Snapshot, sid)
            category = stale.get(Category, cid)
            stale.commit()
            with self.sessions.begin() as writer:
                review.lock_snapshot(writer, sid).status = "failed"
            self.assertEqual(snapshot.status, "extracted")
            with self.assertRaises(AppError) as error:
                review.lock_snapshot(stale, sid)
            self.assertEqual(error.exception.error_code, "REVIEW_SOURCE_NOT_READY")
            self.assertEqual(snapshot.status, "failed")
            self.assertIs(stale.get(Category, cid), category)

    def test_concurrent_publish_refreshes_pointer_and_rejects_stale_expected(self):
        cid, sid, _, revisions = self.seed()
        with self.sessions() as writer:
            first = publisher.publish(writer, sid, self.publish_body([revisions[0]]))

            def competing(reader):
                # 用独立连接预读未提交的旧值，保持缓存引用直到锁后。
                cached_category = reader.get(Category, cid)
                cached_snapshot = reader.get(Snapshot, sid)
                self.assertIsNone(cached_category.active_publication_id)
                try:
                    publisher.publish(reader, sid, self.publish_body([revisions[1]]))
                except AppError as error:
                    self.assertEqual(cached_category.active_publication_id, first.id)
                    self.assertEqual(cached_snapshot.status, "published")
                    return error.error_code
                self.fail("过期指针覆盖已提交发布")

            self.assertEqual(self.contended(writer, competing), "PUBLICATION_POINTER_CONFLICT")
        self.assertEqual(self.state(cid)["pointers"], [first.id, sid])

    def test_publish_two_snapshots_and_rollback(self):
        cid, s1, _, r1 = self.seed()
        _, s2, _, r2 = self.seed(cid)
        with self.sessions.begin() as db:
            p1 = publisher.publish(db, s1, self.publish_body([r1[0]]))
        with self.sessions.begin() as db:
            p2 = publisher.publish(db, s2, self.publish_body([r2[0], r2[1]], p1.id))
        self.assertEqual(self.state(cid)["pointers"], [p2.id, s2])
        with self.sessions.begin() as db:
            result = publisher.rollback(
                db,
                cid,
                RollbackRequest(
                    publication_id=p1.id, expected_active_publication_id=p2.id, comment="跨快照回滚"
                ),
            )
            self.assertEqual(result.payload_json, p1.payload_json)
            self.assertEqual(result.payload_hash, p1.payload_hash)
        self.assertEqual(self.state(cid)["pointers"], [p1.id, s1])

    def failure(self, comment):
        cid, sid, _, revisions = self.seed()
        _, new_sid, _, new_revisions = self.seed(cid)
        with self.sessions.begin() as db:
            first = publisher.publish(db, sid, self.publish_body([revisions[0]]))
        before = self.state(cid)
        with self.assertRaises(DBAPIError) as error:
            with self.sessions.begin() as db:
                publisher.publish(
                    db, new_sid, self.publish_body([new_revisions[0]], first.id, comment)
                )
        diagnostic = database_diagnostic(error.exception, "isolated-postgres-test")
        self.assertEqual(diagnostic["sqlstate"], "23514")
        self.assertEqual(diagnostic["constraint_name"], "p14_injected_switch_failure")
        self.assertEqual(self.state(cid), before)

    def test_failure_after_pointer_update_rolls_back_everything(self):
        self.failure("inject-immediate")

    def test_commit_time_constraint_failure_rolls_back_everything(self):
        self.failure("inject-deferred")

    def test_stale_identity_map_cannot_rollback_new_publication(self):
        cid, sid, _, revisions = self.seed()
        with self.sessions.begin() as db:
            p1 = publisher.publish(db, sid, self.publish_body([revisions[0]]))
        with self.sessions() as stale:
            cached = stale.get(Category, cid)
            stale.commit()
            with self.sessions.begin() as db:
                p2 = publisher.publish(db, sid, self.publish_body([revisions[1]], p1.id))
            with self.assertRaises(AppError) as error:
                publisher.rollback(
                    stale,
                    cid,
                    RollbackRequest(
                        publication_id=p1.id,
                        expected_active_publication_id=p1.id,
                        comment="缓存旧指针不得覆盖",
                    ),
                )
            self.assertEqual(error.exception.error_code, "PUBLICATION_POINTER_CONFLICT")
            self.assertEqual(cached.active_publication_id, p2.id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
