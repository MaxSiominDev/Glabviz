CREATE TABLE IF NOT EXISTS saved_analyses (
    analysis_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    instance_base_url VARCHAR NOT NULL,
    project_id BIGINT NOT NULL,
    config_json VARCHAR NOT NULL,
    config_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS saved_analyses_name_idx
    ON saved_analyses(instance_base_url, project_id, name);

CREATE INDEX IF NOT EXISTS saved_analyses_updated_idx
    ON saved_analyses(updated_at);
