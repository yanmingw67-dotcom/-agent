"""对指定验收快照作只读审计；输出仅含分类计数。"""

import json
import re
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import PurePosixPath

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.index_models import SourceFile
from app.db.models import Snapshot
from app.db.session import session_factory
from app.ingestion.text import decode_source, digest, sensitive_text, source_path

SID = "31675257-2c36-46cf-986f-da166ee2381d"
SHA = "cf3c8c88fb4f48ce5219cd0d0a0ae4841638acec75a5a69d429129565a67fcbf"
CANDIDATES = set(
    "sh bash sql lua xml toml ini conf proto c h cpp rs tsx jsx cjs mjs vue mk tpl".split()
)


def audit():
    counts = defaultdict(Counter)
    size = 0
    with session_factory()() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        snap = db.get(Snapshot, SID)
        if (
            not snap
            or snap.source_sha256 != SHA
            or snap.category_id != "69659364-0781-4038-abc7-d01f3dfa6594"
        ):
            raise ValueError("SNAPSHOT_MISMATCH")
        for f in db.scalars(select(SourceFile).where(SourceFile.snapshot_id == SID)):
            counts["status"][f.parse_status] += 1
            counts["sensitive"][str(f.sensitive)] += 1
            if f.language != "unknown":
                continue
            p = PurePosixPath(f.path)
            ext = p.suffix.lower().lstrip(".")
            counts["extensions"][
                ext if re.fullmatch("[a-z0-9]{1,10}", ext) else ("other" if ext else "none")
            ] += 1
            directory = "/".join(p.parts[:-1][:2])
            counts["directories"][sha256(directory.encode()).hexdigest()[:12]] += 1
            counts["unknown_sensitive"][str(f.sensitive)] += 1
            if f.parse_status == "excluded" or f.sensitive:
                counts["classification"][f.error_code or "sensitive"] += 1
                continue
            path = source_path(get_settings().source_root, SID, f.path)
            if path.stat().st_size != f.size or f.size > get_settings().index_max_file_bytes:
                raise ValueError("SIZE_MISMATCH")
            data = path.read_bytes()
            if digest(data) != f.sha256:
                raise ValueError("HASH_MISMATCH")
            try:
                body, encoding = decode_source(data)
            except UnicodeError:
                counts["classification"]["encoding_unknown"] += 1
                continue
            if chr(0) in body or sensitive_text(body):
                counts["classification"]["restricted_content"] += 1
                continue
            counts["encoding"][encoding] += 1
            size += len(data)
            kind = (
                ext
                if ext in CANDIDATES
                else p.name.lower()
                if p.name.lower() in {"makefile", "dockerfile"}
                else "script"
                if body.startswith("#!")
                else None
            )
            auxiliary = {i.lower() for i in p.parts} & {
                "vendor",
                "third_party",
                "tests",
                "fixtures",
                "examples",
                "test",
                "example",
                "node_modules",
                "dist",
            }
            group = "auxiliary" if auxiliary else "candidate" if kind else "other_text"
            counts["classification"][group] += 1
            if group == "candidate":
                counts["candidate_types"][kind] += 1
    return dict(snapshot=SID, archive_sha256=SHA, readable_unknown_bytes=size, **counts)


if __name__ == "__main__":
    print(json.dumps(audit(), sort_keys=True, indent=2))
