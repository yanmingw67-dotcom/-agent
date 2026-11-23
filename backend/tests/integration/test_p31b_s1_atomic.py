import pytest
from sqlalchemy import select

from app.db.index_models import SourceFile
from app.db.models import Snapshot
from app.db.session import session_factory
from app.ingestion import supplement
from tests.conftest import make_zip
from tests.integration.test_p12_index_retrieval import indexed


def test_late_failure_rolls_back_entire_supplement(client, monkeypatch):
    cid, sid = indexed(client, data=make_zip([("a.xyz", "first"), ("b.xyz", "second")]))
    seen = []

    def fail_late(sid, record, settings, transaction):
        row = transaction.scalar(select(SourceFile).where(SourceFile.path == record["path"]))
        row.parse_status = "text_chunked"
        transaction.flush()
        seen.append(row.id)
        if len(seen) == 2:
            raise RuntimeError("injected")
        return "text_chunked"

    monkeypatch.setattr(supplement, "index_file", fail_late)
    with pytest.raises(RuntimeError, match="injected"), session_factory().begin() as db:
        supplement.supplement(db, sid, cid, db.get(Snapshot, sid).source_sha256)
    with session_factory()() as db:
        assert len(seen) == 2 and db.get(Snapshot, sid).index_status == "indexed"
        assert all(db.get(SourceFile, key).parse_status == "unsupported" for key in seen)
