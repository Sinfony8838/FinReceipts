CREATE TABLE tenants (
 id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) NOT NULL,
 max_pending INT NOT NULL DEFAULT 500, max_running INT NOT NULL DEFAULT 4,
 budget_micros BIGINT NOT NULL DEFAULT 10000000,
 spent_micros BIGINT NOT NULL DEFAULT 0, reserved_micros BIGINT NOT NULL DEFAULT 0,
 last_dispatch BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE members (
 id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL,
 username VARCHAR(100) NOT NULL UNIQUE, password_hash VARCHAR(100) NOT NULL,
 role VARCHAR(20) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE,
 FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
CREATE TABLE api_tokens (
 digest CHAR(64) PRIMARY KEY, member_id VARCHAR(36) NOT NULL,
 scope VARCHAR(20) NOT NULL, expires_at BIGINT NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE,
 FOREIGN KEY (member_id) REFERENCES members(id)
);
CREATE TABLE execution_profiles (
 id CHAR(64) PRIMARY KEY, document JSON NOT NULL, created_at BIGINT NOT NULL
);
CREATE TABLE batches (
 id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL, creator_id VARCHAR(36) NOT NULL,
 request_key VARCHAR(100) NOT NULL, request_digest CHAR(64) NOT NULL,
 title VARCHAR(120) NOT NULL, item_count INT NOT NULL, created_at BIGINT NOT NULL,
 UNIQUE KEY uq_batch_request(tenant_id,creator_id,request_key),
 INDEX ix_batch_page(tenant_id,created_at,id), FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
CREATE TABLE tasks (
 id VARCHAR(36) PRIMARY KEY, batch_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(36) NOT NULL,
 creator_id VARCHAR(36) NOT NULL, profile_id CHAR(64) NOT NULL,
 request JSON NOT NULL, checkpoint JSON NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'QUEUED', step VARCHAR(16) NOT NULL DEFAULT 'PREPARE',
 revision BIGINT NOT NULL DEFAULT 1, lease_token VARCHAR(36), lease_until BIGINT,
 machine_verdict VARCHAR(24) NOT NULL DEFAULT 'PENDING', review_status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
 result JSON, evidence JSON, evidence_digest CHAR(64), error_code VARCHAR(100),
 dispatches INT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
 INDEX ix_task_page(tenant_id,created_at,id), INDEX ix_task_batch(tenant_id,batch_id,id),
 INDEX ix_task_schedule(status,tenant_id,created_at), INDEX ix_task_lease(status,lease_until),
 FOREIGN KEY (batch_id) REFERENCES batches(id), FOREIGN KEY (profile_id) REFERENCES execution_profiles(id)
);
CREATE TABLE events (
 task_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(36) NOT NULL, seq BIGINT NOT NULL,
 kind VARCHAR(40) NOT NULL, document JSON NOT NULL, created_at BIGINT NOT NULL,
 PRIMARY KEY(task_id,seq), INDEX ix_events_tenant(tenant_id,task_id,seq)
);
CREATE TABLE outbox (
 id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36) NOT NULL, revision BIGINT NOT NULL,
 published BOOLEAN NOT NULL DEFAULT FALSE, created_at BIGINT NOT NULL,
 UNIQUE KEY uq_outbox_step(task_id,revision), INDEX ix_outbox_publish(published,created_at)
);
CREATE TABLE attempts (
 id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(36) NOT NULL,
 lease_token VARCHAR(36) NOT NULL, status VARCHAR(24) NOT NULL,
 reserved_micros BIGINT NOT NULL, actual_micros BIGINT, usage_json JSON,
 created_at BIGINT NOT NULL, settled_at BIGINT,
 INDEX ix_attempt_task(task_id), INDEX ix_attempt_status(status)
);
CREATE TABLE provider_budget (
 id VARCHAR(32) PRIMARY KEY, limit_micros BIGINT NOT NULL,
 spent_micros BIGINT NOT NULL DEFAULT 0, reserved_micros BIGINT NOT NULL DEFAULT 0
);
INSERT INTO provider_budget(id,limit_micros) VALUES ('global',20000000);
CREATE TABLE reviews (
 id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(36) NOT NULL,
 reviewer_id VARCHAR(36) NOT NULL, result_revision BIGINT NOT NULL,
 decision VARCHAR(16) NOT NULL, reason VARCHAR(2000) NOT NULL, created_at BIGINT NOT NULL,
 UNIQUE KEY uq_review_version(task_id,result_revision)
);
CREATE TABLE commits (
 task_id VARCHAR(36) NOT NULL, lease_token VARCHAR(36) NOT NULL,
 digest CHAR(64) NOT NULL, revision BIGINT NOT NULL, PRIMARY KEY(task_id,lease_token)
);
CREATE TABLE archives (
 id CHAR(64) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL, creator_id VARCHAR(36) NOT NULL,
 document LONGBLOB NOT NULL, created_at BIGINT NOT NULL, INDEX ix_archive_tenant(tenant_id,id)
);
