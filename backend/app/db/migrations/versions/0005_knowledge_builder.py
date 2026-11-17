"""P1.3 暂存能力、不可变证据索引和模块调查检查点。"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE evidence (\n\tid VARCHAR(80) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tsource_ref JSON NOT NULL, \n\tlocator JSON NOT NULL, \n\tsource_hash VARCHAR(64) NOT NULL, \n\tquote_hash VARCHAR(64) NOT NULL, \n\tverification_state VARCHAR(32) NOT NULL, \n\twarnings_json JSON NOT NULL, \n\tcreated_at TIMESTAMP NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (verification_state = 'source_verified'), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_evidence_snapshot_id ON evidence (snapshot_id)")
    op.execute(
        "\nCREATE TABLE module_investigations (\n\tid VARCHAR(36) NOT NULL, \n\tjob_id VARCHAR(36) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tmodule_id VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tevidence_ids JSON NOT NULL, \n\toutput_json JSON NOT NULL, \n\tmetrics_json JSON NOT NULL, \n\terror_code VARCHAR(64), \n\tattempt INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (job_id, module_id), \n\tFOREIGN KEY(job_id) REFERENCES knowledge_jobs (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(module_id) REFERENCES modules (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_module_investigations_job_id ON module_investigations (job_id)")
    op.execute(
        "CREATE INDEX ix_module_investigations_snapshot_id ON module_investigations (snapshot_id)"
    )
    op.execute(
        "\nCREATE TABLE capability_claims (\n\tid VARCHAR(80) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tinvestigation_id VARCHAR(36) NOT NULL, \n\tlocal_key VARCHAR(80) NOT NULL, \n\tname VARCHAR(1000) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tdraft_json JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (investigation_id, local_key), \n\tCHECK (status IN ('candidate', 'evidence_found')), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(investigation_id) REFERENCES module_investigations (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_capability_claims_investigation_id ON capability_claims (investigation_id)"
    )
    op.execute("CREATE INDEX ix_capability_claims_snapshot_id ON capability_claims (snapshot_id)")
    op.execute(
        "\nCREATE TABLE investigation_traces (\n\tid VARCHAR(36) NOT NULL, \n\tinvestigation_id VARCHAR(36) NOT NULL, \n\tattempt INTEGER NOT NULL, \n\tsequence INTEGER NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tdetails_json JSON NOT NULL, \n\tcreated_at TIMESTAMP NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (investigation_id, attempt, sequence), \n\tFOREIGN KEY(investigation_id) REFERENCES module_investigations (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_investigation_traces_investigation_id ON investigation_traces (investigation_id)"
    )
    op.execute(
        "\nCREATE TABLE capability_relations (\n\tid VARCHAR(80) NOT NULL, \n\tsnapshot_id VARCHAR(36) NOT NULL, \n\tinvestigation_id VARCHAR(36) NOT NULL, \n\tsource_claim_id VARCHAR(80) NOT NULL, \n\ttarget_claim_id VARCHAR(80) NOT NULL, \n\trelation_type VARCHAR(32) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tdetails_json JSON NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (status = 'hypothesis'), \n\tFOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), \n\tFOREIGN KEY(investigation_id) REFERENCES module_investigations (id), \n\tFOREIGN KEY(source_claim_id) REFERENCES capability_claims (id), \n\tFOREIGN KEY(target_claim_id) REFERENCES capability_claims (id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_capability_relations_snapshot_id ON capability_relations (snapshot_id)"
    )
    op.execute(
        "\nCREATE TABLE claim_evidence_links (\n\tclaim_id VARCHAR(80) NOT NULL, \n\tevidence_id VARCHAR(80) NOT NULL, \n\tsupport_type VARCHAR(40) NOT NULL, \n\tsupported_statement TEXT NOT NULL, \n\tPRIMARY KEY (claim_id, evidence_id), \n\tFOREIGN KEY(claim_id) REFERENCES capability_claims (id), \n\tFOREIGN KEY(evidence_id) REFERENCES evidence (id)\n)\n\n"
    )


def downgrade():
    op.drop_table("claim_evidence_links")
    op.drop_table("capability_relations")
    op.drop_table("investigation_traces")
    op.drop_table("capability_claims")
    op.drop_table("module_investigations")
    op.drop_table("evidence")
