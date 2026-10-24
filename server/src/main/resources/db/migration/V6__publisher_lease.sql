ALTER TABLE outbox ADD COLUMN publish_token VARCHAR(36), ADD COLUMN publish_until BIGINT NOT NULL DEFAULT 0;
CREATE INDEX ix_outbox_lease ON outbox(published,publish_until);
