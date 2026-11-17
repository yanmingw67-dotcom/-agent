"""P1.2 索引实体；业务事实/证据发布不在本批实现。"""

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, uid


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("snapshot_id", "path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(32))
    encoding: Mapped[str | None] = mapped_column(String(32))
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_status: Mapped[str] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    sensitive: Mapped[bool] = mapped_column(default=False)
    module_id: Mapped[str | None] = mapped_column(String(36), index=True)


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    qualified_name: Mapped[str] = mapped_column(String(500), index=True)
    signature: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)


class CodeEdge(Base):
    __tablename__ = "code_edges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id"), index=True)
    from_symbol_id: Mapped[str | None] = mapped_column(ForeignKey("code_symbols.id"))
    to_symbol_id: Mapped[str | None] = mapped_column(ForeignKey("code_symbols.id"))
    target_name: Mapped[str] = mapped_column(String(500))
    line: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    resolution_status: Mapped[str] = mapped_column(String(40))


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("product_categories.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    filename: Mapped[str] = mapped_column(String(240))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(40), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    parse_details: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


class DocumentSegment(Base):
    __tablename__ = "document_segments"
    __table_args__ = (
        Index(
            "ix_document_text_fts", text("to_tsvector('simple', text)"), postgresql_using="gin"
        ).ddl_if(dialect="postgresql"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    locator: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    sensitive: Mapped[bool] = mapped_column(default=False)


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("snapshot_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    path_globs: Mapped[list] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text, default="")
    analysis_status: Mapped[str] = mapped_column(String(40), default="heuristic")


class Entrypoint(Base):
    __tablename__ = "entrypoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"), index=True)
    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[dict] = mapped_column(JSON)
    investigation_status: Mapped[str] = mapped_column(String(40), default="unresolved_candidate")
