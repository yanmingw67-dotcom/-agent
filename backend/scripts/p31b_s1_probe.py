"""只读验收：真实符号、路径和正文只在进程内比较。"""

import json
from collections import Counter, defaultdict

from sqlalchemy import select, text

from app.core.security import AppError
from app.db.index_models import CodeSymbol, SourceFile
from app.db.session import session_factory
from app.retrieval.schemas import ToolRequest
from app.retrieval.tools import ReadOnlyTools, bind_context

SID = "31675257-2c36-46cf-986f-da166ee2381d"
CAT = "69659364-0781-4038-abc7-d01f3dfa6594"


def probe():
    metrics = defaultdict(Counter)
    with session_factory()() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        tools = ReadOnlyTools(db, bind_context(db, CAT, "admin", SID))

        def run(tool, **kwargs):
            return tools.execute(ToolRequest(tool=tool, **kwargs))

        for f in db.scalars(select(SourceFile).where(SourceFile.snapshot_id == SID)):
            metrics["status"][f.parse_status] += 1
            if f.parse_status == "parsed":
                metrics["structured_languages"][f.language] += 1
            if (
                not f.sensitive
                and f.encoding
                and f.parse_status in {"parsed", "text_chunked", "unsupported", "syntax_error"}
            ):
                metrics["eligible_text"][f.language] += 1
        for term in ("policy", "rule", "threshold", "route", "config", "audit"):
            r = run("search_source", query=term, limit=5)
            metrics["keyword_hits"][term] = len(r.results)
            if r.results:
                item = r.results[0]
                evidence = run(
                    "read_source",
                    file_id=item.file_id,
                    start_line=item.start_line,
                    end_line=item.start_line,
                ).results[0]
                metrics["keyword_readback"][term] = int(
                    evidence.sha256 == item.sha256 and term in evidence.snippet
                )
        for language in (
            "go",
            "python",
            "typescript",
            "tsx",
            "javascript",
            "yaml",
            "json",
            "markdown",
            "shell",
        ):
            row = db.execute(
                select(CodeSymbol, SourceFile)
                .join(SourceFile, SourceFile.id == CodeSymbol.file_id)
                .where(
                    SourceFile.snapshot_id == SID,
                    SourceFile.language == language,
                    SourceFile.sensitive.is_(False),
                    CodeSymbol.qualified_name != "",
                )
                .order_by(SourceFile.path, CodeSymbol.start_line)
                .limit(1)
            ).first()
            if not row:
                continue
            sym, f = row
            item = run("read_source", symbol_id=sym.id).results[0]
            metrics["symbol_readback"][language] = int(
                item.file_id == f.id
                and item.start_line == sym.start_line
                and item.sha256 == f.sha256
                and bool(item.snippet)
            )
            metrics["module_context"][language] = int(
                run("get_module_context", module_id=f.module_id, limit=1).module_context is not None
            )
        sensitive = list(
            db.scalars(
                select(SourceFile.id).where(
                    SourceFile.snapshot_id == SID, SourceFile.sensitive.is_(True)
                )
            )
        )
        for fid in sensitive:
            try:
                run("read_source", file_id=fid)
            except AppError as exc:
                metrics["protected"][exc.error_code] += 1
        try:
            bind_context(db, CAT, "read", SID)
        except AppError as exc:
            metrics["protected"][exc.error_code] += 1
    return metrics


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True, indent=2))
