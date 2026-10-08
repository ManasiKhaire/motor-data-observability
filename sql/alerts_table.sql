-- One shared table where every check writes the problems it finds.
-- The orchestrator agent reads new rows from here and groups them into incidents.
-- Change the catalog and schema names to match your Databricks workspace.

CREATE TABLE IF NOT EXISTS main.motor_obs.alerts (
  alert_id     STRING    NOT NULL COMMENT 'Unique id, e.g. uuid',
  source       STRING    NOT NULL COMMENT 'telematics | policy | claims | garage_bills | customer_kyc',
  step         STRING    NOT NULL COMMENT 'Node id from lineage/lineage.json, e.g. bronze_claims',
  check_name   STRING    NOT NULL COMMENT 'freshness | volume | schema | null | range | duplicate | reconciliation | pii',
  problem      STRING    NOT NULL COMMENT 'Plain description, e.g. 6 of 20 rows have negative claim_amount',
  severity     STRING    NOT NULL COMMENT 'LOW | MEDIUM | HIGH | CRITICAL',
  detected_at  TIMESTAMP NOT NULL,
  batch_file   STRING             COMMENT 'Landing file that triggered it',
  sample_rows  STRING             COMMENT 'Up to 5 bad rows as JSON, PII masked',
  incident_id  STRING             COMMENT 'Filled by the orchestrator agent'
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.motor_obs.incidents (
  incident_id    STRING    NOT NULL,
  opened_at      TIMESTAMP NOT NULL,
  status         STRING    NOT NULL COMMENT 'OPEN | AWAITING_APPROVAL | FIXING | CLOSED',
  root_cause     STRING,
  affected_gold  ARRAY<STRING>,
  proposed_fix   STRING,
  approved_by    STRING,
  closed_at      TIMESTAMP,
  report         STRING
) USING DELTA;
