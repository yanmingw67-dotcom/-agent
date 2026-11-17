import json
import os
import selectors
import subprocess
import tempfile
import time

from sqlalchemy import func, or_, select
from sqlalchemy import text as sql_text

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import (
    CodeEdge,
    CodeSymbol,
    Document,
    DocumentSegment,
    Entrypoint,
    Module,
    SourceFile,
)
from app.db.models import Category, Snapshot
from app.ingestion.text import decode_source, digest, source_path
from app.retrieval.schemas import (
    ModuleContextView,
    RetrievalItem,
    RunContext,
    ToolEnvelope,
    ToolRequest,
)


def bind_context(db, category_id, role, snapshot_id=None):
    category = db.get(Category, category_id)
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "产品品类不存在")
    if snapshot_id and role != "admin":
        raise AppError(403, "STAGING_FORBIDDEN", "只读角色不能选择后台快照")
    sid = snapshot_id or category.active_snapshot_id
    if not sid:
        raise AppError(409, "KNOWLEDGE_NOT_PUBLISHED", "当前品类没有已发布知识库")
    snapshot = db.get(Snapshot, sid)
    if not snapshot or snapshot.category_id != category_id:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    if not snapshot_id and snapshot.status != "published":
        raise AppError(409, "KNOWLEDGE_NOT_PUBLISHED", "知识库未发布")
    return RunContext(
        category_id=category_id,
        snapshot_id=sid,
        principal_id=role,
        policy="admin_staging" if snapshot_id else "published",
    )


class ReadOnlyTools:
    """服务端绑定作用域；工具请求无品类、快照、磁盘根路径或执行命令字段。"""

    def __init__(self, db, context: RunContext):
        self.db, self.context = db, context
        self.settings = get_settings()
        snapshot = db.get(Snapshot, context.snapshot_id)
        if not snapshot or snapshot.category_id != context.category_id:
            raise AppError(404, "SNAPSHOT_NOT_FOUND", "作用域不匹配")
        if context.policy == "admin_staging":
            if context.principal_id != "admin":
                raise AppError(403, "FORBIDDEN", "仅管理员可调查暂存数据")
        else:
            category = db.get(Category, context.category_id)
            if snapshot.status != "published" or category.active_snapshot_id != snapshot.id:
                raise AppError(409, "KNOWLEDGE_NOT_PUBLISHED", "作用域不是当前生效知识库")

    def file(self, file_id):
        file = self.db.scalar(
            select(SourceFile).where(
                SourceFile.id == file_id, SourceFile.snapshot_id == self.context.snapshot_id
            )
        )
        if not file:
            raise AppError(404, "SOURCE_NOT_FOUND", "作用域内没有该文件")
        if file.sensitive:
            raise AppError(403, "SENSITIVE_SOURCE_RESTRICTED", "敏感文件暂不允许返回原文")
        if file.parse_status in {"excluded", "oversized", "invalid_encoding"}:
            raise AppError(422, "SOURCE_UNREADABLE", "文件未进入可回读文本索引")
        return file

    def read(self, file, start, end):
        start_time = time.monotonic()
        path = source_path(self.settings.source_root, self.context.snapshot_id, file.path)
        if path.stat().st_size > self.settings.index_max_file_bytes:
            raise AppError(413, "READ_SIZE_LIMIT", "原文超过回读限额")
        data = path.read_bytes()
        if digest(data) != file.sha256:
            raise AppError(409, "SOURCE_HASH_MISMATCH", "来源哈希发生变化")
        text, _ = decode_source(data)
        lines = text.splitlines()
        if start > len(lines) or end < start:
            raise AppError(422, "INVALID_LINE_RANGE", "行号范围无效")
        end = min(end, len(lines), start + self.settings.read_max_lines - 1)
        if time.monotonic() - start_time > self.settings.search_timeout_seconds:
            raise AppError(504, "SEARCH_TIMEOUT", "读取超时")
        return RetrievalItem(
            file_id=file.id,
            path=file.path,
            sha256=file.sha256,
            start_line=start,
            end_line=end,
            snippet="\n".join(lines[start - 1 : end]),
        )

    def execute(self, request: ToolRequest):
        begin = time.monotonic()
        sid, db, s = self.context.snapshot_id, self.db, self.settings
        limit = min(request.limit, 30 if request.tool == "search_code_batch" else 100)
        envelope = ToolEnvelope(
            snapshot_id=sid, tool=request.tool, warnings=["semantic_index_unavailable"]
        )
        items = []
        tool = request.tool
        files = select(SourceFile).where(
            SourceFile.snapshot_id == sid, SourceFile.sensitive.is_(False)
        )
        if request.module_ids:
            files = files.where(SourceFile.module_id.in_(request.module_ids))
        if tool == "get_project_map":
            rows = db.scalars(
                select(Module)
                .where(Module.snapshot_id == sid)
                .order_by(Module.name)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            items = [
                RetrievalItem(
                    id=m.id,
                    module_id=m.id,
                    name=m.name,
                    status=m.analysis_status,
                    snippet=m.summary,
                )
                for m in rows
            ]
            envelope.warnings += ["module_map_heuristic", "coverage_not_investigated"]
        elif tool == "get_module_context":
            module = db.scalar(
                select(Module).where(Module.id == request.module_id, Module.snapshot_id == sid)
            )
            if not module:
                raise AppError(404, "MODULE_NOT_FOUND", "作用域内没有该模块")
            rows = db.scalars(
                files.where(SourceFile.module_id == module.id)
                .order_by(SourceFile.path)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            items = [self.file_item(f) for f in rows]
            symbol_rows = db.execute(
                select(CodeSymbol, SourceFile)
                .join(SourceFile, SourceFile.id == CodeSymbol.file_id)
                .where(
                    CodeSymbol.snapshot_id == sid,
                    SourceFile.snapshot_id == sid,
                    SourceFile.module_id == module.id,
                    SourceFile.sensitive.is_(False),
                )
                .order_by(CodeSymbol.qualified_name)
                .limit(11)
            ).all()
            symbols = [
                RetrievalItem(
                    symbol_id=sym.id,
                    file_id=f.id,
                    path=f.path,
                    name=sym.qualified_name,
                    kind=sym.kind,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    snippet=sym.signature,
                )
                for sym, f in symbol_rows[:10]
            ]
            document_rows = db.execute(
                select(DocumentSegment, Document)
                .join(Document, Document.id == DocumentSegment.document_id)
                .where(
                    DocumentSegment.snapshot_id == sid,
                    Document.category_id == self.context.category_id,
                    Document.snapshot_id == sid,
                    Document.parse_status.in_(["parsed", "partial"]),
                    DocumentSegment.sensitive.is_(False),
                    DocumentSegment.text.contains(module.name, autoescape=True),
                )
                .order_by(DocumentSegment.id)
                .limit(6)
            ).all()
            doc_hints = [
                RetrievalItem(
                    id=seg.id,
                    document_id=doc.id,
                    path=doc.filename,
                    snippet=seg.text[:500],
                    locator=seg.locator,
                    sha256=seg.text_hash,
                    unresolved=True,
                )
                for seg, doc in document_rows[:5]
            ]
            envelope.module_context = ModuleContextView(
                **{
                    "symbols": [item.model_dump() for item in symbols],
                    "document_candidates": [item.model_dump() for item in doc_hints],
                    "dependencies": [],
                    "dependencies_status": "unresolved",
                    "initial_clues": ["directory_and_AST_only; not a product capability"],
                    "truncated": len(symbol_rows) > 10 or len(document_rows) > 5,
                    "document_matching": "module_name_lexical_candidates_not_verified_links",
                }
            )
            envelope.warnings.append("dependency_semantics_unresolved")
        elif tool == "search_paths":
            rows = db.scalars(
                files.where(SourceFile.path.contains(request.query, autoescape=True))
                .order_by(SourceFile.path)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            items = [self.file_item(f) for f in rows]
        elif tool == "search_symbols":
            query = (
                select(CodeSymbol, SourceFile)
                .join(SourceFile, SourceFile.id == CodeSymbol.file_id)
                .where(
                    CodeSymbol.snapshot_id == sid,
                    SourceFile.snapshot_id == sid,
                    SourceFile.sensitive.is_(False),
                    CodeSymbol.qualified_name.contains(request.query, autoescape=True),
                )
            )
            if request.kinds:
                query = query.where(CodeSymbol.kind.in_(request.kinds))
            if request.module_ids:
                query = query.where(SourceFile.module_id.in_(request.module_ids))
            rows = db.execute(
                query.order_by(CodeSymbol.qualified_name, CodeSymbol.id)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            items = [
                RetrievalItem(
                    id=sym.id,
                    file_id=f.id,
                    symbol_id=sym.id,
                    path=f.path,
                    name=sym.qualified_name,
                    kind=sym.kind,
                    snippet=sym.signature,
                    sha256=f.sha256,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    role=f.role,
                )
                for sym, f in rows
            ]
        elif tool == "read_source":
            start, end, fid = request.start_line, request.end_line, request.file_id
            if request.symbol_id:
                symbol = db.scalar(
                    select(CodeSymbol).where(
                        CodeSymbol.id == request.symbol_id, CodeSymbol.snapshot_id == sid
                    )
                )
                if not symbol or (fid and fid != symbol.file_id):
                    raise AppError(404, "SYMBOL_NOT_FOUND", "作用域内没有该符号")
                fid, start, end = symbol.file_id, symbol.start_line, symbol.end_line
            if not fid:
                raise AppError(422, "FILE_REQUIRED", "需要文件或符号 ID")
            f = self.file(fid)
            requested_end = end or start + s.read_max_lines - 1
            item = self.read(f, start, requested_end)
            envelope.truncated = requested_end > item.end_line and item.end_line < f.line_count
            items = [item]
        elif tool == "list_entrypoints":
            query = (
                select(Entrypoint, SourceFile)
                .join(SourceFile, SourceFile.id == Entrypoint.file_id)
                .where(
                    Entrypoint.snapshot_id == sid,
                    SourceFile.snapshot_id == sid,
                    SourceFile.sensitive.is_(False),
                )
            )
            if request.module_id:
                query = query.where(Entrypoint.module_id == request.module_id)
            if request.types:
                query = query.where(Entrypoint.type.in_(request.types))
            rows = db.execute(
                query.order_by(Entrypoint.id).offset(request.cursor).limit(limit + 1)
            ).all()
            items = [
                RetrievalItem(
                    id=e.id,
                    module_id=e.module_id,
                    file_id=f.id,
                    path=f.path,
                    kind=e.type,
                    locator=e.source_ref,
                    status=e.investigation_status,
                    unresolved=True,
                )
                for e, f in rows
            ]
        elif tool == "find_references":
            symbol = db.scalar(
                select(CodeSymbol).where(
                    CodeSymbol.id == request.symbol_id, CodeSymbol.snapshot_id == sid
                )
            )
            if not symbol:
                raise AppError(404, "SYMBOL_NOT_FOUND", "作用域内没有该符号")
            self.file(symbol.file_id)
            name = symbol.qualified_name.split(".")[-1]
            query = (
                select(CodeEdge, SourceFile)
                .join(SourceFile, SourceFile.id == CodeEdge.file_id)
                .where(
                    CodeEdge.snapshot_id == sid,
                    SourceFile.snapshot_id == sid,
                    SourceFile.sensitive.is_(False),
                    (CodeEdge.target_name == name)
                    | CodeEdge.target_name.endswith("." + name, autoescape=True),
                )
            )
            rows = db.execute(
                query.order_by(SourceFile.path, CodeEdge.line, CodeEdge.id)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            items = [
                RetrievalItem(
                    id=e.id,
                    file_id=f.id,
                    path=f.path,
                    name=e.target_name,
                    start_line=e.line,
                    kind="syntactic_reference",
                    unresolved=True,
                    status=e.resolution_status,
                )
                for e, f in rows
            ]
            envelope.warnings.append("name_based_candidates_not_semantic_call_graph")
        elif tool in {"search_documents", "read_document"}:
            query = (
                select(DocumentSegment, Document)
                .join(Document, Document.id == DocumentSegment.document_id)
                .where(
                    DocumentSegment.snapshot_id == sid,
                    Document.snapshot_id == sid,
                    Document.category_id == self.context.category_id,
                    Document.parse_status.in_(["parsed", "partial"]),
                    DocumentSegment.sensitive.is_(False),
                )
            )
            if tool == "read_document":
                query = query.where(DocumentSegment.id == request.segment_id)
            else:
                # FTS 索引可供英文词检索；LIKE 保证中文子串和准确原文定位。
                contains = DocumentSegment.text.contains(request.query, autoescape=True)
                if (
                    db.bind.dialect.name == "postgresql"
                    and request.query.isascii()
                    and request.query.strip()
                ):
                    fts = sql_text(
                        "to_tsvector('simple', document_segments.text) @@ plainto_tsquery('simple', :fts_query)"
                    ).bindparams(fts_query=request.query)
                    query = query.where(or_(fts, contains))
                else:
                    query = query.where(contains)
            rows = db.execute(
                query.order_by(Document.id, DocumentSegment.id)
                .offset(request.cursor)
                .limit(limit + 1)
            ).all()
            if tool == "read_document" and not rows:
                raise AppError(404, "SEGMENT_NOT_FOUND", "作用域内没有可读取段落")
            items = [
                RetrievalItem(
                    id=seg.id,
                    document_id=doc.id,
                    path=doc.filename,
                    locator=seg.locator,
                    snippet=seg.text,
                    sha256=seg.text_hash,
                    status=doc.parse_status,
                )
                for seg, doc in rows
            ]
            if any(doc.parse_status == "partial" for _, doc in rows):
                envelope.warnings.append("partial_document_pages; inspect document parse_details")
        elif tool == "search_code_batch":
            items, cut, warnings = self.search_code(request, files, begin)
            envelope.truncated = cut
            envelope.warnings.extend(warnings)
        if len(items) > limit:
            envelope.truncated = True
            envelope.next_cursor = str(request.cursor + limit)
        remaining = s.retrieval_max_chars
        if envelope.module_context:
            for contextual_item in (
                envelope.module_context.symbols + envelope.module_context.document_candidates
            ):
                if contextual_item.snippet:
                    original = contextual_item.snippet
                    contextual_item.snippet = original[: max(0, remaining)]
                    remaining -= len(contextual_item.snippet)
                    if len(contextual_item.snippet) < len(original):
                        envelope.module_context.truncated = envelope.truncated = True
        for item in items[:limit]:
            if item.snippet:
                if len(item.snippet) > remaining:
                    item.snippet = item.snippet[: max(0, remaining)]
                    envelope.truncated = True
                    envelope.warnings.append("context_truncated")
                remaining -= len(item.snippet)
            envelope.results.append(item)
            if remaining <= 0:
                envelope.truncated = True
                break
        if tool in {
            "search_code_batch",
            "search_symbols",
            "get_project_map",
            "get_module_context",
            "list_entrypoints",
        }:
            unreadable = ["oversized", "excluded", "invalid_encoding"]
            if tool != "search_code_batch":
                unreadable += ["unsupported", "syntax_error"]
            skipped = db.execute(
                files.with_only_columns(SourceFile.parse_status, func.count())
                .where(SourceFile.parse_status.in_(unreadable))
                .group_by(SourceFile.parse_status)
            ).all()
            if skipped:
                envelope.coverage_incomplete = True
                envelope.warnings.append(
                    "source_coverage_incomplete:" + json.dumps(dict(skipped), sort_keys=True)
                )
            if db.get(Snapshot, sid).index_status != "indexed":
                envelope.coverage_incomplete = True
                envelope.warnings.append("source_index_not_complete")
        if tool == "search_documents":
            unfinished = db.execute(
                select(Document.parse_status, func.count())
                .where(
                    Document.snapshot_id == sid,
                    Document.category_id == self.context.category_id,
                    Document.parse_status != "parsed",
                )
                .group_by(Document.parse_status)
            ).all()
            if unfinished:
                envelope.coverage_incomplete = True
                envelope.warnings.append(
                    "document_coverage_incomplete:" + json.dumps(dict(unfinished), sort_keys=True)
                )
        if tool == "read_document" and any(item.status == "partial" for item in items):
            envelope.coverage_incomplete = True
        if tool == "search_code_batch" and envelope.truncated:
            envelope.next_cursor = str(request.cursor + len(envelope.results))
        elif envelope.next_cursor is not None:
            envelope.next_cursor = str(request.cursor + len(envelope.results))
        envelope.duration_ms = round((time.monotonic() - begin) * 1000, 3)
        return envelope

    @staticmethod
    def file_item(f):
        return RetrievalItem(
            file_id=f.id,
            path=f.path,
            language=f.language,
            role=f.role,
            status=f.parse_status,
            sha256=f.sha256,
            module_id=f.module_id,
        )

    def search_code(self, request, file_query, begin):
        terms = request.queries or [request.query]
        if not terms or any(not q or len(q) > 200 or "\x00" in q for q in terms):
            raise AppError(422, "INVALID_SEARCH", "查询不能为空且每条不超过 200 字符")
        s = self.settings
        query = file_query.where(
            SourceFile.parse_status.in_(["parsed", "unsupported", "syntax_error"]),
            SourceFile.encoding == "utf-8-sig",
        ).order_by(SourceFile.path)
        items, seen = [], set()
        # keyset 分页，每批 64 个已索引且非敏感文件，不递归扫描未授权目录。
        offset = 0
        while True:
            if time.monotonic() - begin > s.search_timeout_seconds:
                return items, True, ["search_timeout_partial"]
            files = self.db.scalars(query.offset(offset).limit(64)).all()
            if not files:
                return self.search_encoded(request, file_query, begin, items, seen)
            mapping = {
                str(source_path(s.source_root, self.context.snapshot_id, f.path)): f for f in files
            }
            command = [
                "rg",
                "--json",
                "--no-config",
                "--color",
                "never",
                "--sort",
                "path",
                # 全局已跳过数量+本页+前瞻；到达该上限前必已返回可继续页。
                "--max-count",
                str(request.cursor + min(request.limit, 30) + 1),
                "--max-columns",
                "2000",
                "--max-columns-preview",
                "--regex-size-limit",
                "1M",
                "--dfa-size-limit",
                "1M",
            ]
            if request.mode == "literal":
                command.append("--fixed-strings")
            for term in terms:
                command += ["-e", term]
            command += ["--", *mapping]
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
            except FileNotFoundError as exc:
                raise AppError(503, "RIPGREP_UNAVAILABLE", "ripgrep 未安装") from exc
            buffer = b""
            cut = False
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        remaining = s.search_timeout_seconds - (time.monotonic() - begin)
                        if remaining <= 0 or not selector.select(max(0, remaining)):
                            cut = True
                            break
                        block = os.read(process.stdout.fileno(), 65536)
                        if not block:
                            break
                        buffer += block
                        if len(buffer) > 1024 * 1024:
                            cut = True
                            break
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            event = json.loads(line)
                            if event["type"] != "match":
                                continue
                            match = event["data"]
                            absolute = match["path"].get("text")
                            file = mapping.get(absolute)
                            if not file:
                                continue
                            line_no = match["line_number"]
                            if (file.id, line_no) in seen:
                                continue
                            seen.add((file.id, line_no))
                            if len(seen) <= request.cursor:
                                continue
                            item = self.read(file, line_no, line_no)
                            item.match_terms = terms
                            items.append(item)
                            if len(items) > min(request.limit, 30):
                                cut = True
                                break
                        if cut:
                            break
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
            if cut:
                return items, True, ["result_or_time_budget_reached"]
            if process.returncode not in {0, 1}:
                raise AppError(422, "INVALID_SEARCH_REGEX", "正则表达式不合法或超限")
            offset += len(files)

    def search_encoded(self, request, file_query, begin, items, seen):
        query = file_query.where(
            SourceFile.encoding.in_(["utf-16", "utf-32"]),
            SourceFile.parse_status.in_(["parsed", "unsupported", "syntax_error"]),
        )
        offset = 0
        while True:
            files = self.db.scalars(query.order_by(SourceFile.path).offset(offset).limit(16)).all()
            if not files:
                return items, False, []
            for file in files:
                remaining = self.settings.search_timeout_seconds - (time.monotonic() - begin)
                if remaining <= 0:
                    return items, True, ["search_timeout_partial"]
                path = source_path(self.settings.source_root, self.context.snapshot_id, file.path)
                if path.stat().st_size > self.settings.index_max_file_bytes:
                    continue
                data = path.read_bytes()
                if digest(data) != file.sha256:
                    raise AppError(409, "SOURCE_HASH_MISMATCH", "来源哈希发生变化")
                decoded, _ = decode_source(data)
                args = [
                    "rg",
                    "--json",
                    "--no-config",
                    "--max-count",
                    str(request.cursor + min(request.limit, 30) + 1),
                    "--regex-size-limit",
                    "1M",
                    "--dfa-size-limit",
                    "1M",
                ]
                if request.mode == "literal":
                    args.append("--fixed-strings")
                for term in request.queries or [request.query]:
                    args.extend(["-e", term])
                args += ["--", "-"]
                # 输出落受限/tmp而非把大量JSON一次加载到内存；原始源码只读。
                with tempfile.TemporaryFile() as output:
                    try:
                        result = subprocess.run(
                            args,
                            input=decoded.encode(),
                            stdout=output,
                            stderr=subprocess.DEVNULL,
                            timeout=remaining,
                        )
                    except subprocess.TimeoutExpired:
                        return items, True, ["search_timeout_partial"]
                    except FileNotFoundError as exc:
                        raise AppError(503, "RIPGREP_UNAVAILABLE", "ripgrep 未安装") from exc
                    if result.returncode not in {0, 1}:
                        raise AppError(422, "INVALID_SEARCH_REGEX", "正则表达式不合法或超限")
                    output.seek(0)
                    for line in output:
                        if time.monotonic() - begin > self.settings.search_timeout_seconds:
                            return items, True, ["search_timeout_partial"]
                        event = json.loads(line)
                        if event["type"] != "match":
                            continue
                        line_no = event["data"]["line_number"]
                        if (file.id, line_no) in seen:
                            continue
                        seen.add((file.id, line_no))
                        if len(seen) <= request.cursor:
                            continue
                        item = self.read(file, line_no, line_no)
                        item.match_terms = request.queries or [request.query]
                        items.append(item)
                        if len(items) > min(request.limit, 30):
                            return items, True, ["result_budget_reached"]
            offset += len(files)
