"""真实PG Probe一致性测试，复用历史隔离schema夹具；直接运行需同时加载基类。"""

import unittest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.knowledge import publisher
from app.knowledge.review_schemas import RollbackRequest
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
