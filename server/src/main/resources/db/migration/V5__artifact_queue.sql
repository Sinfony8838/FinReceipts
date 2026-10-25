CREATE TABLE artifact_copies (
 digest CHAR(64) PRIMARY KEY, kind VARCHAR(16) NOT NULL, document LONGBLOB NOT NULL,
 created_at BIGINT NOT NULL, copied BOOLEAN NOT NULL DEFAULT FALSE,
 INDEX ix_artifact_pending(copied,created_at)
);
