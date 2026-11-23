import json
import resource
import time
from contextlib import nullcontext
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.index_models import (
    CodeEdge,
    CodeSymbol,
    Document,
    DocumentSegment,
    Entrypoint,
    Module,
    SourceFile,
)
from app.db.models import KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.ingestion.doc_parsers import DocumentError, parse_document
from app.ingestion.language_adapters import analyze, language_for
from app.ingestion.resources import ResourceSampler
from app.ingestion.text import decode_source, digest, sensitive_text, source_path


def index_file(snapshot_id, record, settings, transaction=None):
    path = record["path"]
    version = settings.index_parser_version + "-s1"
    with nullcontext(transaction) if transaction is not None else session_factory().begin() as db:
        file = db.scalar(
            select(SourceFile).where(SourceFile.snapshot_id == snapshot_id, SourceFile.path == path)
        )
        if (
            file
            and file.sha256 == record["sha256"]
            and (
                file.parser_version == version
                or (transaction is None and file.parser_version == settings.index_parser_version)
            )
        ):
            return "reused"
    language = language_for(path)
    text = ""
    encoding = None
    status, error, symbols, calls, entries = "unsupported", None, [], [], []
    restricted = record.get("sensitive_detected", False)
    if record.get("reason"):
        status, error = "excluded", record["reason"]
    elif record["size"] > settings.index_max_file_bytes:
        status, error = "oversized", "INDEX_FILE_SIZE_LIMIT"
    else:
        actual_path = source_path(settings.source_root, snapshot_id, path)
        if actual_path.stat().st_size != record["size"]:
            raise ValueError("SOURCE_HASH_MISMATCH")
        data = actual_path.read_bytes()
        if digest(data) != record["sha256"]:
            raise ValueError("SOURCE_HASH_MISMATCH")
        try:
            text, encoding = decode_source(data)
            restricted |= sensitive_text(text)
            language = language_for(path, text)
            if restricted:
                status = "restricted"
            elif chr(0) in text:
                status, error = "invalid_encoding", "BINARY_CONTENT"
            else:
                status, symbols, calls, entries = analyze(text, language)
        except UnicodeError:
            status, error = "invalid_encoding", "INVALID_ENCODING"
    module_name = path.split("/")[0] if "/" in path else "__root__"
    with nullcontext(transaction) if transaction is not None else session_factory().begin() as db:
        module = db.scalar(
            select(Module).where(Module.snapshot_id == snapshot_id, Module.name == module_name)
        )
        if not module:
            module = Module(
                snapshot_id=snapshot_id,
                name=module_name,
                path_globs=[module_name + "/**" if module_name != "__root__" else "*"],
                summary="目录候选；业务含义未验证",
                analysis_status="heuristic",
            )
            db.add(module)
            db.flush()
        file = db.scalar(
            select(SourceFile).where(SourceFile.snapshot_id == snapshot_id, SourceFile.path == path)
        )
        if file and transaction is None:
            db.execute(delete(CodeEdge).where(CodeEdge.file_id == file.id))
            db.execute(delete(Entrypoint).where(Entrypoint.file_id == file.id))
            db.execute(delete(CodeSymbol).where(CodeSymbol.file_id == file.id))
        elif not file:
            file = SourceFile(snapshot_id=snapshot_id, path=path)
            db.add(file)
        file.sha256, file.size, file.language = record["sha256"], record["size"], language
        file.role = (
            "test"
            if "test" in path.split("/")
            or "tests" in path.split("/")
            or Path(path).name.startswith("test_")
            else "source"
        )
        file.encoding, file.line_count = encoding, len(text.splitlines())
        if language in {"markdown", "text", "yaml", "json"}:
            parts = {p.lower() for p in Path(path).parts}
            stem = Path(path).stem.lower()
            file.role = (
                "third_party"
                if parts & {"vendor", "third_party", "node_modules"}
                else "example"
                if parts & {"example", "examples", "samples"} or "example" in stem
                else "planned"
                if stem in {"roadmap", "todo"}
                else "history"
                if stem in {"changelog", "history"}
                else "development"
                if stem in {"contributing", "development"}
                else "config_candidate"
                if language in {"yaml", "json"}
                else "document_unverified"
            )
        file.parse_status, file.error_code, file.parser_version = status, error, version
        file.sensitive, file.module_id = restricted, module.id
        db.flush()
        created = []
        for values in symbols:
            symbol = CodeSymbol(snapshot_id=snapshot_id, file_id=file.id, **values)
            db.add(symbol)
            created.append(symbol)
        db.flush()
        for call in calls:
            # 不推断运行时动态绑定，即使同名也标记待解析。
            db.add(
                CodeEdge(
                    snapshot_id=snapshot_id,
                    file_id=file.id,
                    from_symbol_id=created[call["owner"]].id if call["owner"] is not None else None,
                    to_symbol_id=None,
                    target_name=call["target_name"],
                    line=call["line"],
                    kind="call",
                    resolution_status="unresolved_syntactic",
                )
            )
        if language in {"yaml", "json"} and not restricted and transaction is None:
            entries.append(dict(type="config", line=1, basis="configuration filename heuristic"))
        for entry in entries:
            db.add(
                Entrypoint(
                    snapshot_id=snapshot_id,
                    module_id=module.id,
                    file_id=file.id,
                    type=entry["type"],
                    source_ref={"path": path, "start_line": entry["line"], "basis": entry["basis"]},
                )
            )
    return status


def index_document(document_id, settings):
    with session_factory()() as db:
        doc = db.get(Document, document_id)
        if doc.parser_version == settings.index_parser_version and doc.parse_status == "parsed":
            return "reused"
        key, sha, suffix, sid = (
            doc.storage_key,
            doc.sha256,
            Path(doc.filename).suffix.lower(),
            doc.snapshot_id,
        )
    path = settings.document_store / key
    code = None
    details = {}
    partial = False
    try:
        if path.stat().st_size > settings.document_max_bytes:
            raise DocumentError("DOCUMENT_TOO_LARGE")
        if digest(path.read_bytes()) != sha:
            raise DocumentError("DOCUMENT_HASH_MISMATCH")
        with session_factory().begin() as db:
            db.execute(delete(DocumentSegment).where(DocumentSegment.document_id == document_id))
            # 受限文档输出在单文档事务内，失败不暴露部分内容。
            for locator, text in parse_document(path, suffix, settings, details):
                db.add(
                    DocumentSegment(
                        snapshot_id=sid,
                        document_id=document_id,
                        locator=locator,
                        text=text,
                        text_hash=digest(text.encode()),
                        sensitive=sensitive_text(text),
                    )
                )
                db.flush()
                db.expunge_all()
        partial = any(p["status"] != "parsed" for p in details.get("pages", []))
    except DocumentError as exc:
        code = exc.code
    with session_factory().begin() as db:
        doc = db.get(Document, document_id)
        doc.parse_status = (
            "ocr_required"
            if code == "OCR_REQUIRED"
            else ("failed" if code else ("partial" if partial else "parsed"))
        )
        doc.error_code, doc.parser_version = code, settings.index_parser_version
        doc.parse_details = details
    return code or ("partial" if partial else "parsed")


def run_index(job_id, token):
    settings = get_settings()
    start, cpu = time.monotonic(), time.process_time()
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.dispatch_token != token:
            return
        job.status, job.started_at = "running", now()
        sid, job_type = job.snapshot_id, job.job_type
        snapshot = db.get(Snapshot, sid)
        if snapshot.status == "published":
            job.status, job.stage, job.error_code = (
                "failed",
                "failed",
                "IMMUTABLE_PUBLISHED_SNAPSHOT",
            )
            return
        if job_type == "source_index":
            snapshot.index_status = "indexing"
    metrics = dict(
        indexed_files=0,
        reused_files=0,
        documents=0,
        model_requests=0,
        tokens=0,
        model_cost=0,
        model_mode="NOT_USED",
        semantic_index="semantic_index_unavailable",
    )
    error = None
    sampler = ResourceSampler()
    sampler.start()
    try:
        if job_type == "source_index":
            manifest = json.loads((settings.source_root / sid / "manifest.json").read_text())
            with session_factory()() as db:
                if manifest["source_sha256"] != db.get(Snapshot, sid).source_sha256:
                    raise ValueError("MANIFEST_HASH_MISMATCH")
            # 清单有入库总条目限额；源码本体始终一次一个文件。
            for record in manifest["files"] + manifest["excluded"]:
                result = index_file(sid, record, settings)
                metrics["reused_files" if result == "reused" else "indexed_files"] += 1
                with session_factory().begin() as db:
                    db.get(KnowledgeJob, job_id).metrics_json = dict(metrics)
        with session_factory()() as db:
            ids = list(db.scalars(select(Document.id).where(Document.snapshot_id == sid)))
        for doc_id in ids:
            result = index_document(doc_id, settings)
            metrics["documents"] += 1
            if result not in {"parsed", "reused"}:
                metrics["document_errors"] = metrics.get("document_errors", 0) + 1
    except Exception as exc:
        error = (
            str(exc)
            if str(exc) in {"SOURCE_HASH_MISMATCH", "MANIFEST_HASH_MISMATCH"}
            else "INDEX_INTERNAL_ERROR"
        )
    metrics.update(sampler.finish())
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import sys

    metrics.update(
        duration_ms=round((time.monotonic() - start) * 1000),
        cpu_seconds=round(time.process_time() - cpu, 3),
        peak_rss_bytes=int(rss if sys.platform == "darwin" else rss * 1024),
    )
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if job.dispatch_token != token or job.status != "running":
            return
        job.status = (
            "failed" if error else ("partial" if metrics.get("document_errors") else "succeeded")
        )
        job.stage = "failed" if error else "indexed"
        job.error_code, job.metrics_json, job.finished_at = error, metrics, now()
        snapshot = db.get(Snapshot, sid)
        if job_type == "source_index":
            snapshot.index_status = "failed" if error else "indexed"
