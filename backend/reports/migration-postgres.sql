BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE product_categories (
    id VARCHAR(36) NOT NULL, 
    code VARCHAR(64) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    active_snapshot_id VARCHAR(36), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE source_snapshots (
    id VARCHAR(36) NOT NULL, 
    category_id VARCHAR(36) NOT NULL, 
    source_sha256 VARCHAR(64) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    archive_key VARCHAR(100) NOT NULL, 
    manifest_key VARCHAR(200), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CHECK (status IN ('uploaded','validating','extracted','failed','published')), 
    FOREIGN KEY(category_id) REFERENCES product_categories (id), 
    UNIQUE (archive_key)
);

CREATE INDEX ix_source_snapshots_category_id ON source_snapshots (category_id);

ALTER TABLE product_categories ADD CONSTRAINT fk_active_snapshot FOREIGN KEY(active_snapshot_id) REFERENCES source_snapshots (id);

CREATE TABLE knowledge_jobs (
    id VARCHAR(36) NOT NULL, 
    snapshot_id VARCHAR(36) NOT NULL, 
    category_id VARCHAR(36) NOT NULL, 
    job_type VARCHAR(32) NOT NULL, 
    stage VARCHAR(32) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    error_code VARCHAR(64), 
    metrics_json JSON NOT NULL, 
    attempt INTEGER NOT NULL, 
    dispatch_token VARCHAR(36), 
    dispatched_at TIMESTAMP WITH TIME ZONE, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CHECK (status IN ('queued','running','succeeded','failed','partial','cancelled')), 
    FOREIGN KEY(snapshot_id) REFERENCES source_snapshots (id), 
    FOREIGN KEY(category_id) REFERENCES product_categories (id)
);

CREATE INDEX ix_knowledge_jobs_snapshot_id ON knowledge_jobs (snapshot_id);

CREATE UNIQUE INDEX uq_active_category_import ON knowledge_jobs (category_id) WHERE status IN ('queued','running');

CREATE TABLE audit_events (
    id VARCHAR(36) NOT NULL, 
    principal_id VARCHAR(32) NOT NULL, 
    action VARCHAR(64) NOT NULL, 
    resource_id VARCHAR(36) NOT NULL, 
    correlation_id VARCHAR(36) NOT NULL, 
    details_json JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE FUNCTION guard_active_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.active_snapshot_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM source_snapshots s WHERE s.id = NEW.active_snapshot_id
            AND s.category_id = NEW.id AND s.status = 'published'
          ) THEN RAISE EXCEPTION 'active snapshot must be published and category-scoped'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER category_snapshot_guard BEFORE INSERT OR UPDATE ON product_categories
        FOR EACH ROW EXECUTE FUNCTION guard_active_snapshot();
        CREATE FUNCTION guard_snapshot_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.category_id <> OLD.category_id OR NEW.source_sha256 <> OLD.source_sha256
             OR NEW.archive_key <> OLD.archive_key THEN
            RAISE EXCEPTION 'snapshot identity is immutable';
          END IF;
          IF NEW.status <> 'published' AND EXISTS (
            SELECT 1 FROM product_categories WHERE active_snapshot_id = OLD.id
          ) THEN RAISE EXCEPTION 'active snapshot must remain published'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER snapshot_identity_guard BEFORE UPDATE ON source_snapshots
        FOR EACH ROW EXECUTE FUNCTION guard_snapshot_identity();;

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

