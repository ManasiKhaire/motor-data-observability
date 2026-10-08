"""The local data store: one SQLite file standing in for Delta tables on Databricks.

Layers kept as tables:
  bronze               every raw row exactly as it arrived (JSON), per source
  silver_*             cleaned, typed, masked rows that passed the checks
  quarantine           rows stopped by a check, with the reason
  gold_*               final data products, rebuilt every cycle unless blocked
Observability tables:
  alerts, incidents, incident_events, node_status, gold_refresh, column_mappings,
  file_registry, pipeline_runs
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_registry (
  file TEXT PRIMARY KEY, source TEXT, rows INTEGER, ingested_at TEXT);

CREATE TABLE IF NOT EXISTS bronze (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, batch_file TEXT,
  payload TEXT, ingested_at TEXT);
CREATE INDEX IF NOT EXISTS ix_bronze_source ON bronze(source, batch_file);

CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, batch_file TEXT, check_name TEXT,
  reason TEXT, payload TEXT, quarantined_at TEXT, released INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS silver_telematics (
  device_id TEXT, vehicle_id TEXT, policy_id TEXT, ts TEXT, speed_kmph REAL,
  harsh_brake INTEGER, harsh_accel INTEGER, lat REAL, lon REAL, odometer_km REAL, batch_file TEXT);
CREATE TABLE IF NOT EXISTS silver_policy (
  policy_id TEXT PRIMARY KEY, customer_id TEXT, vehicle_id TEXT, reg_no TEXT, make TEXT, model TEXT,
  year INTEGER, cover_type TEXT, sum_insured REAL, premium REAL, start_date TEXT, end_date TEXT,
  change_type TEXT, batch_file TEXT);
CREATE TABLE IF NOT EXISTS silver_claims (
  claim_id TEXT PRIMARY KEY, policy_id TEXT, vehicle_id TEXT, accident_date TEXT, reported_at TEXT,
  location TEXT, claim_type TEXT, claim_amount REAL, status TEXT, garage_id TEXT, batch_file TEXT);
CREATE TABLE IF NOT EXISTS silver_garage_bills (
  bill_id TEXT PRIMARY KEY, claim_id TEXT, garage_id TEXT, parts_cost REAL, labour_cost REAL,
  total REAL, invoice_date TEXT, batch_file TEXT);
CREATE TABLE IF NOT EXISTS silver_customer (
  customer_id TEXT PRIMARY KEY, name TEXT, phone TEXT, email TEXT, licence_no TEXT, pan TEXT,
  address TEXT, dob TEXT, city TEXT, updated_at TEXT, masked INTEGER, batch_file TEXT);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY, source TEXT, step TEXT, check_name TEXT, problem TEXT, severity TEXT,
  first_seen TEXT, last_seen TEXT, occurrences INTEGER, clean_batches INTEGER DEFAULT 0,
  status TEXT, incident_id TEXT, sample TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY, source TEXT, title TEXT, status TEXT, severity TEXT,
  opened_at TEXT, root_cause TEXT, category TEXT, confidence REAL, evidence TEXT, impact TEXT,
  proposed_fix TEXT, fix_action TEXT, fix_params TEXT, decided_by TEXT, decided_at TEXT,
  test_result TEXT, closed_at TEXT, report TEXT, llm_used INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT, ts TEXT, actor TEXT, message TEXT);

CREATE TABLE IF NOT EXISTS node_status (
  node TEXT PRIMARY KEY, status TEXT, detail TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS gold_refresh (
  node TEXT PRIMARY KEY, last_refreshed TEXT, blocked INTEGER DEFAULT 0, reason TEXT);

CREATE TABLE IF NOT EXISTS column_mappings (
  source TEXT, from_col TEXT, to_col TEXT, added_at TEXT, PRIMARY KEY (source, from_col));

CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  files INTEGER, rows INTEGER, new_alerts INTEGER);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # dashboard can read while the pipeline writes
    conn.executescript(SCHEMA)
    return conn


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def to_json(value) -> str:
    return json.dumps(value, default=str)


def from_json(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default
