"""Bronze -> Silver: rename mapped columns, mask personal data, write typed rows.

PII masking needs a valid Vault token (output/control/vault_token.json).
This pipeline deliberately has a realistic bug: when the token is expired, masking
fails OPEN, so raw personal data slips into Silver. The PII scan catches it and
the agents trace it back to the token. That is the main demo story.
"""
from pathlib import Path

from common.io import log_event, read_json, utc_now

SILVER_COLUMNS = {
    "telematics": ["device_id", "vehicle_id", "policy_id", "ts", "speed_kmph", "harsh_brake",
                   "harsh_accel", "lat", "lon", "odometer_km"],
    "policy": ["policy_id", "customer_id", "vehicle_id", "reg_no", "make", "model", "year", "cover_type",
               "sum_insured", "premium", "start_date", "end_date", "change_type"],
    "claims": ["claim_id", "policy_id", "vehicle_id", "accident_date", "reported_at", "location",
               "claim_type", "claim_amount", "status", "garage_id"],
    "garage_bills": ["bill_id", "claim_id", "garage_id", "parts_cost", "labour_cost", "total", "invoice_date"],
    "customer_kyc": ["customer_id", "name", "phone", "email", "licence_no", "pan", "address", "dob",
                     "city", "updated_at", "masked"],
}
SILVER_TABLE = {"telematics": "silver_telematics", "policy": "silver_policy", "claims": "silver_claims",
                "garage_bills": "silver_garage_bills", "customer_kyc": "silver_customer"}


# ---------- masking ----------
def mask_value(col, val):
    if val is None:
        return None
    s = str(val)
    if col == "name":
        return " ".join(p[0] + "***" for p in s.split() if p)
    if col == "phone":
        return s[:3] + "******" + s[-4:]
    if col == "email":
        user, _, domain = s.partition("@")
        return (user[:1] + "***@" + domain) if domain else "***"
    if col == "pan":
        return "*****" + s[5:9] + "*" if len(s) == 10 else "**********"
    if col == "licence_no":
        return s[:4] + "*" * max(0, len(s) - 4)
    if col == "address":
        return "*** (masked)"
    if col == "dob":
        return s[:4] + "-**-**"
    return s


PII_COLUMNS = ["name", "phone", "email", "licence_no", "pan", "address", "dob"]


def mask_row(row):
    out = dict(row)
    for col in PII_COLUMNS:
        if col in out:
            out[col] = mask_value(col, out[col])
    return out


def vault_token_valid(out_dir: Path) -> bool:
    return read_json(Path(out_dir) / "control" / "vault_token.json", {"status": "valid"}).get("status") != "expired"


# ---------- column mappings (the schema-drift fix writes these) ----------
def get_mappings(conn, source):
    return {r["from_col"]: r["to_col"] for r in conn.execute(
        "SELECT from_col, to_col FROM column_mappings WHERE source=?", (source,))}


def apply_mappings(rows, mappings):
    if not mappings:
        return rows
    out = []
    for r in rows:
        new = {}
        for k, v in r.items():
            target = mappings.get(k, k)
            if target:  # an empty target means "ignore this column"
                new[target] = v
        out.append(new)
    return out


# ---------- write Silver ----------
def to_silver(conn, out_dir, source, rows, batch_file):
    """Insert checked rows. Returns how many KYC rows went in unmasked (0 for other sources)."""
    if not rows:
        return 0
    unmasked = 0
    if source == "customer_kyc":
        if vault_token_valid(out_dir):
            rows = [dict(mask_row(r), masked=1) for r in rows]
        else:
            log_event(out_dir, "pipeline", "ERROR",
                      "mask_pii: Vault returned 403 on token refresh; masking skipped, rows passed through",
                      component="mask_pii", related_source="customer_kyc", rows=len(rows),
                      batch_file=batch_file)
            rows = [dict(r, masked=0) for r in rows]
            unmasked = len(rows)
    if source == "telematics":
        rows = [dict(r, ts=r.get("timestamp")) for r in rows]

    cols = SILVER_COLUMNS[source] + ["batch_file"]
    verb = "INSERT" if source == "telematics" else "INSERT OR REPLACE"
    conn.executemany(
        f"{verb} INTO {SILVER_TABLE[source]} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [tuple(_sql(r.get(c)) for c in SILVER_COLUMNS[source]) + (batch_file,) for r in rows])
    return unmasked


def _sql(v):
    if isinstance(v, bool):
        return int(v)
    return v


def remask_silver_customer(conn):
    """Used by the fix: mask every row that slipped through unmasked."""
    leaked = [dict(r) for r in conn.execute("SELECT * FROM silver_customer WHERE masked=0")]
    for r in leaked:
        m = mask_row(r)
        conn.execute(f"UPDATE silver_customer SET {', '.join(f'{c}=?' for c in PII_COLUMNS)}, masked=1 "
                     "WHERE customer_id=?", tuple(m[c] for c in PII_COLUMNS) + (r["customer_id"],))
    return len(leaked)


def now_iso():
    return utc_now().isoformat()
