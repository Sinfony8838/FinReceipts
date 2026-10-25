CREATE INDEX ix_batch_cursor ON batches(tenant_id,id);
CREATE INDEX ix_task_cursor ON tasks(tenant_id,id);
CREATE INDEX ix_task_review ON tasks(tenant_id,status,review_status,id);
CREATE INDEX ix_attempt_lease ON attempts(task_id,lease_token,status);
