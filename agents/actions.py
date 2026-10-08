"""The only actions the platform can take. Each has a test (on a small sample) and an apply.

Demo note: actions that in real life need the source team (restart a connector, fix a feed)
also switch off the injected problem in output/control/inject.json, standing in for that fix.
"""
import json
from pathlib import Path

from common.io import set_injection, set_vault_token
from pipeline import checks, gold
from pipeline.alerts import resolve
from pipeline.db import rows
from pipeline.transform import mask_row, now_iso, remask_silver_customer, to_silver


def _quarantined(conn, source, check_name=None, limit=None):
    sql = "SELECT * FROM quarantine WHERE source=? AND released=0"
    params = [source]
    if check_name:
        sql += " AND check_name=?"
        params.append(check_name)
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return rows(conn, sql, params)


def _apply_mapping(row, params):
    out = {}
    for k, v in row.items():
        if k in params.get("drop", []):
            continue
        out[params.get("rename", {}).get(k, k)] = v
    return out


# ---------- tests on a small sample ----------
def test(conn, out_dir, action, params):
    """Returns (passed, message). Nothing here changes real data."""
    source = params.get("source")
    if action == "renew_vault_token":
        leaked = rows(conn, "SELECT * FROM silver_customer WHERE masked=0 LIMIT 20")
        if not leaked:
            return True, "No leaked rows left to re-mask; nothing to test."
        sample = [dict(mask_row(r), masked=1) for r in leaked]
        still = checks.scan_pii(sample)
        return (not still, f"Re-masked a sample of {len(sample)} leaked rows with a fresh token: "
                           f"{len(sample) - len(still)} clean, {len(still)} still readable.")

    if action == "add_column_mapping":
        sample = [_apply_mapping(json.loads(q["payload"]), params)
                  for q in _quarantined(conn, source, "schema", limit=20)]
        if not sample:
            return True, "No quarantined schema rows to test."
        good, bad, findings = checks.check_batch(source, sample, {"policy_end": {}})
        schema_ok = not any(f.check_name == "schema" for f in findings)
        return (schema_ok and len(good) > 0,
                f"Applied the mapping to {len(sample)} quarantined rows: {len(good)} now pass, {len(bad)} still fail"
                + ("" if schema_ok else " (columns still differ from the contract)."))

    if action == "restart_connector":
        return True, f"Connection test for the {source} connector passed in the sandbox."

    n = len(_quarantined(conn, source))
    return True, f"{n} bad {source} rows are held in quarantine; none of them reached Silver."


# ---------- apply for real ----------
def apply(conn, out_dir, action, params):
    """Returns a message describing what changed."""
    out_dir, source = Path(out_dir), params.get("source")
    if action == "renew_vault_token":
        set_injection(out_dir, "customer_kyc", "off")  # demo: the token problem stops at the source too
        set_vault_token(out_dir, "valid")
        fixed = remask_silver_customer(conn)
        for node in ("gold_customer_360", "gold_compliance_report"):
            gold.unblock(conn, node)
        resolve(conn, "customer_kyc", "pii")
        return f"Vault token renewed. Re-masked {fixed} rows in silver_customer. Gold publishing released."

    if action == "add_column_mapping":
        for frm, to in params.get("rename", {}).items():
            conn.execute("INSERT OR REPLACE INTO column_mappings VALUES (?, ?, ?, ?)", (source, frm, to, now_iso()))
        for col in params.get("drop", []):
            conn.execute("INSERT OR REPLACE INTO column_mappings VALUES (?, ?, '', ?)", (source, col, now_iso()))
        reloaded = 0
        for q in _quarantined(conn, source, "schema"):
            row = _apply_mapping(json.loads(q["payload"]), params)
            good, _, _ = checks.check_batch(source, [row], {"policy_end": {}})
            if good:
                to_silver(conn, out_dir, source, good, q["batch_file"])
                conn.execute("UPDATE quarantine SET released=1 WHERE id=?", (q["id"],))
                reloaded += 1
        resolve(conn, source, "schema")
        return (f"Mapping saved for {source}; future files load automatically. "
                f"Reloaded {reloaded} quarantined rows into Silver.")

    if action == "restart_connector":
        set_injection(out_dir, source, "off")
        return f"{source} connector restarted. New files will clear the freshness alert on arrival."

    set_injection(out_dir, source, "off")
    return f"Source owner for {source} notified; bad rows stay in quarantine."


def verify(conn, action, params):
    if action == "renew_vault_token":
        leaks = checks.scan_pii(rows(conn, "SELECT * FROM silver_customer"))
        return not leaks, f"PII scan after fix: {len(leaks)} unmasked rows."
    if action == "add_column_mapping":
        left = len(_quarantined(conn, params["source"], "schema"))
        return True, f"{left} schema rows left in quarantine (rows that also had other problems)."
    return True, "Fix applied; the next batches will confirm it."
