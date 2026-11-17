"""P1.2 静态索引数据结构（固定 DDL，不引用可变模型）。"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "source_snapshots",
        sa.Column("index_status", sa.String(32), nullable=False, server_default="not_indexed"),
    )
    op.execute(
        "CREATE TABLE modules (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tname VARCHAR(240) NOT NULL, \n\tpath_globs JSON NOT NULL, \n\tsummary TEXT NOT NULL, \n\tanalysis_status VARCHAR(40) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (snapshot_id, name), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id)\n)"
    )
    op.execute("CREATE INDEX ix_modules_snapshot_id ON modules (snapshot_id)")
    op.execute(
        "CREATE TABLE source_files (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tpath VARCHAR(1000) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize INTEGER NOT NULL, \n\tlanguage VARCHAR(32) NOT NULL, \n\trole VARCHAR(32) NOT NULL, \n\tencoding VARCHAR(32), \n\tline_count INTEGER NOT NULL, \n\tparse_status VARCHAR(40) NOT NULL, \n\terror_code VARCHAR(64), \n\tparser_version VARCHAR(32) NOT NULL, \n\tsensitive BOOLEAN NOT NULL, \n\tmodule_id VARCHAR(36), \n\tPRIMARY KEY (id), \n\tUNIQUE (snapshot_id, path), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id)\n)"
    )
    op.execute("CREATE INDEX ix_source_files_module_id ON source_files (module_id)")
    op.execute("CREATE INDEX ix_source_files_snapshot_id ON source_files (snapshot_id)")
    op.execute(
        "CREATE TABLE code_symbols (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tqualified_name VARCHAR(500) NOT NULL, \n\tsignature TEXT NOT NULL, \n\tstart_line INTEGER NOT NULL, \n\tend_line INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(file_id) REFERENCES source_files (id)\n)"
    )
    op.execute("CREATE INDEX ix_code_symbols_file_id ON code_symbols (file_id)")
    op.execute("CREATE INDEX ix_code_symbols_qualified_name ON code_symbols (qualified_name)")
    op.execute("CREATE INDEX ix_code_symbols_snapshot_id ON code_symbols (snapshot_id)")
    op.execute(
        "CREATE TABLE code_edges (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\tfrom_symbol_id VARCHAR(36), \n\tto_symbol_id VARCHAR(36), \n\ttarget_name VARCHAR(500) NOT NULL, \n\tline INTEGER NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tresolution_status VARCHAR(40) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(file_id) REFERENCES source_files (id), \n\tFOREIGN KEY(from_symbol_id) REFERENCES code_symbols (id), \n\tFOREIGN KEY(to_symbol_id) REFERENCES code_symbols (id)\n)"
    )
    op.execute("CREATE INDEX ix_code_edges_file_id ON code_edges (file_id)")
    op.execute("CREATE INDEX ix_code_edges_snapshot_id ON code_edges (snapshot_id)")
    op.execute(
        "CREATE TABLE documents (\n\tid VARCHAR(36) NOT NULL, \n\tcategory_id VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tfilename VARCHAR(240) NOT NULL, \n\tstorage_key VARCHAR(100) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize INTEGER NOT NULL, \n\tparse_status VARCHAR(40) NOT NULL, \n\terror_code VARCHAR(64), \n\tparser_version VARCHAR(32), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(category_id) REFERENCES product_categories (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tUNIQUE (storage_key)\n)"
    )
    op.execute("CREATE INDEX ix_documents_category_id ON documents (category_id)")
    op.execute("CREATE INDEX ix_documents_snapshot_id ON documents (snapshot_id)")
    op.execute(
        "CREATE TABLE document_segments (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tdocument_id VARCHAR(36) NOT NULL, \n\tlocator JSON NOT NULL, \n\ttext TEXT NOT NULL, \n\ttext_hash VARCHAR(64) NOT NULL, \n\tsensitive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(document_id) REFERENCES documents (id)\n)"
    )
    op.execute("CREATE INDEX ix_document_segments_document_id ON document_segments (document_id)")
    op.execute("CREATE INDEX ix_document_segments_snapshot_id ON document_segments (snapshot_id)")
    op.execute(
        "CREATE TABLE entrypoints (\n\tid VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tmodule_id VARCHAR(36) NOT NULL, \n\tfile_id VARCHAR(36) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tsource_ref JSON NOT NULL, \n\tinvestigation_status VARCHAR(40) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(module_id) REFERENCES modules (id), \n\tFOREIGN KEY(file_id) REFERENCES source_files (id)\n)"
    )
    op.execute("CREATE INDEX ix_entrypoints_file_id ON entrypoints (file_id)")
    op.execute("CREATE INDEX ix_entrypoints_module_id ON entrypoints (module_id)")
    op.execute("CREATE INDEX ix_entrypoints_snapshot_id ON entrypoints (snapshot_id)")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_document_text_fts ON document_segments USING gin (to_tsvector('simple', text))"
        )


def downgrade():
    op.drop_table("entrypoints")
    op.drop_table("document_segments")
    op.drop_table("documents")
    op.drop_table("code_edges")
    op.drop_table("code_symbols")
    op.drop_table("source_files")
    op.drop_table("modules")
    op.drop_column("source_snapshots", "index_status")
