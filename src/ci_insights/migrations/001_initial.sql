CREATE TABLE IF NOT EXISTS app_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    instance_base_url VARCHAR NOT NULL,
    project_id BIGINT NOT NULL,
    path VARCHAR NOT NULL,
    display_name VARCHAR,
    default_branch VARCHAR,
    default_timezone VARCHAR NOT NULL DEFAULT 'UTC',
    local_filter_json VARCHAR NOT NULL DEFAULT '{}',
    fetched_at TIMESTAMP,
    PRIMARY KEY (instance_base_url, project_id)
);

CREATE TABLE IF NOT EXISTS pipelines (
    instance_base_url VARCHAR NOT NULL,
    project_id BIGINT NOT NULL,
    pipeline_id BIGINT NOT NULL,
    iid BIGINT,
    name VARCHAR,
    ref VARCHAR,
    source VARCHAR,
    sha VARCHAR,
    source_status VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at TIMESTAMP,
    gitlab_duration_s DOUBLE,
    web_url VARCHAR,
    fetched_at TIMESTAMP NOT NULL,
    snapshot_completeness VARCHAR NOT NULL,
    snapshot_error VARCHAR,
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    last_attempt_at TIMESTAMP,
    last_attempt_state VARCHAR,
    last_error VARCHAR,
    PRIMARY KEY (instance_base_url, project_id, pipeline_id)
);

CREATE TABLE IF NOT EXISTS job_attempts (
    instance_base_url VARCHAR NOT NULL,
    project_id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    pipeline_id BIGINT NOT NULL,
    exact_name VARCHAR NOT NULL,
    stage VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL,
    observed_failure BOOLEAN NOT NULL,
    is_current_attempt BOOLEAN,
    created_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    gitlab_duration_s DOUBLE,
    queued_duration_s DOUBLE,
    allow_failure BOOLEAN NOT NULL,
    runner_id BIGINT,
    web_url VARCHAR,
    failure_reason VARCHAR,
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    timestamp_validity VARCHAR,
    source_presence VARCHAR NOT NULL,
    PRIMARY KEY (instance_base_url, project_id, job_id)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_id VARCHAR PRIMARY KEY,
    instance_base_url VARCHAR NOT NULL,
    project_id BIGINT NOT NULL,
    requested_start TIMESTAMP NOT NULL,
    requested_end TIMESTAMP NOT NULL,
    requested_timezone VARCHAR NOT NULL,
    requested_start_date DATE NOT NULL,
    requested_end_date DATE NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    state VARCHAR NOT NULL,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    error_summary VARCHAR
);

CREATE INDEX IF NOT EXISTS pipelines_period_idx
    ON pipelines(instance_base_url, project_id, created_at);

CREATE INDEX IF NOT EXISTS job_attempts_pipeline_idx
    ON job_attempts(instance_base_url, project_id, pipeline_id);
