ALTER TABLE tasks
  ADD COLUMN ticker VARCHAR(16) GENERATED ALWAYS AS (JSON_UNQUOTE(request->'$.ticker')) STORED,
  ADD COLUMN fiscal_year INT GENERATED ALWAYS AS (CAST(request->'$.target.fiscal_year' AS UNSIGNED)) STORED,
  ADD COLUMN metric VARCHAR(64) GENERATED ALWAYS AS (JSON_UNQUOTE(request->'$.target.metric')) STORED;
