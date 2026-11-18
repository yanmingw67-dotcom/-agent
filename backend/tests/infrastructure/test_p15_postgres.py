"""真实PG Probe一致性测试，复用历史隔离schema夹具；直接运行需同时加载基类。"""

import unittest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.security import AppError
from app.db.review_models import ClaimRevision
from app.knowledge import publisher, review
from app.knowledge.review_schemas import ReviewRequest, RollbackRequest
from app.retrieval.probe import KnowledgeProbe

# 通过模块方式运行时导入；容器stdin运行器预先载入基类。
if "PostgreSQLRemediationTests" not in globals():
    from tests.infrastructure.test_p14_r1_postgres import PostgreSQLRemediationTests


class ProbePostgresTests(PostgreSQLRemediationTests):
    def test_probe_keeps_request_snapshot_across_publication_switch(self):
        cid, sid, _, revisions = self.seed()
        with self.sessions.begin() as writer:
            first = publisher.publish(writer, sid, self.publish_body([revisions[0]]))
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            before = KnowledgeProbe(reader, cid, "read").retrieve("首项")
            with self.sessions.begin() as writer:
                second = publisher.publish(writer, sid, self.publish_body([revisions[1]], first.id))
            during = KnowledgeProbe(reader, cid, "read").retrieve("首项")
            self.assertEqual(before.publication_id, during.publication_id)
            self.assertEqual(during.candidates[0].revision_id, revisions[0])
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            after = KnowledgeProbe(reader, cid, "read").retrieve("首项")
            self.assertEqual(after.publication_id, second.id)
            self.assertEqual(after.candidates[0].revision_id, revisions[1])
        with self.sessions.begin() as writer:
            publisher.rollback(
                writer,
                cid,
                RollbackRequest(
                    publication_id=first.id,
                    expected_active_publication_id=second.id,
                    comment="Probe真实回滚",
                ),
            )
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            restored = KnowledgeProbe(reader, cid, "read").retrieve("首项")
            self.assertEqual(restored.publication_hash, before.publication_hash)
            self.assertEqual(restored.candidates, before.candidates)

    def test_probe_preserves_frozen_publication_until_publish_or_rollback(self):
        cid, sid, _, revisions = self.seed()
        with self.sessions.begin() as writer:
            first = publisher.publish(writer, sid, self.publish_body([revisions[0]]))
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            before = KnowledgeProbe(reader, cid, "read").retrieve("首项")

        # 新审核修订已接受但未正式发布，不得改变当前冻结清单。
        with self.sessions.begin() as writer:
            old = writer.get(ClaimRevision, revisions[0], populate_existing=True)
            body = ReviewRequest(
                action="accept",
                expected_revision=old.number,
                declaration_type=old.declaration_type,
                content=old.content_json,
                assertions=old.assertions_json,
                reachability_evidence_ids=old.gate_json.get("reachability_evidence_ids", []),
                comment="真实PG测试：新接受但尚未发布",
                human_confirmed=True,
            )
            newer = review.review(writer, old.claim_id, body)
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            frozen = KnowledgeProbe(reader, cid, "read").retrieve("首项")
        self.assertEqual(frozen.publication_id, first.id)
        self.assertEqual(frozen.publication_hash, before.publication_hash)
        self.assertEqual(frozen.candidates[0].revision_id, revisions[0])

        # 新正式发布必须使用最新接受修订，不能用旧修订绕过审批。
        with self.sessions.begin() as writer:
            with self.assertRaises(AppError) as error:
                publisher.publish(writer, sid, self.publish_body([revisions[0]], first.id))
            self.assertEqual(error.exception.error_code, "PUBLICATION_REVIEW_REQUIRED")

        with self.sessions.begin() as writer:
            second = publisher.publish(writer, sid, self.publish_body([newer.id], first.id))
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            switched = KnowledgeProbe(reader, cid, "read").retrieve("首项")
        self.assertEqual(switched.publication_id, second.id)
        self.assertEqual(switched.candidates[0].revision_id, newer.id)
        self.assertNotEqual(switched.publication_hash, before.publication_hash)

        with self.sessions.begin() as writer:
            publisher.rollback(
                writer,
                cid,
                RollbackRequest(
                    publication_id=first.id,
                    expected_active_publication_id=second.id,
                    comment="真实PG测试：回滚恢复冻结清单",
                ),
            )
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            restored = KnowledgeProbe(reader, cid, "read").retrieve("首项")
        self.assertEqual(restored.publication_id, first.id)
        self.assertEqual(restored.publication_hash, before.publication_hash)
        self.assertEqual(restored.candidates, before.candidates)

    def test_probe_readonly_transaction_cannot_update_knowledge(self):
        cid, _, _, _ = self.seed()
        with self.sessions() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            self.assertEqual(reader.scalar(text("SHOW transaction_read_only")), "on")
            with self.assertRaises(DBAPIError) as error:
                reader.execute(
                    text("UPDATE product_categories SET name='forbidden' WHERE id=:id"), {"id": cid}
                )
            self.assertEqual(error.exception.orig.sqlstate, "25006")


if __name__ == "__main__":
    unittest.main(verbosity=2)
