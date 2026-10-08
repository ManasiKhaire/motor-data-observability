"""Data checks. Plain Python rules: fast, free, and run on every batch.

check_batch() looks at one landing file and returns:
  good     rows that may continue to Silver
  bad      (row, check_name, reason) for rows to quarantine
  findings one summary per failed check, which becomes an alert
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

from common.config import ROOT
from pipeline import lineage

CONTRACTS = json.loads((ROOT / "schemas" / "source_contracts.json").read_text(encoding="utf-8"))

SEVERITY = {
    "schema": "HIGH", "nulls": "HIGH", "referential": "HIGH", "reconciliation": "HIGH",
    "freshness": "HIGH", "pii": "CRITICAL", "range": "MEDIUM", "allowed_values": "MEDIUM",
    "format": "MEDIUM", "rule": "MEDIUM", "duplicates": "MEDIUM", "type": "MEDIUM",
}
PHONE_RE = re.compile(r"^\+91\d{10}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


@dataclass
class Finding:
    source: str
    check_name: str
    step: str
    problem: str
    severity: str
    sample: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def contract(source):
    return {k: v for k, v in CONTRACTS[source].items() if not k.startswith("_")}


def _coerce(value, kind):
    """CSV gives strings; turn them into the contract's type. Raises ValueError if impossible."""
    if value is None or value == "":
        return None
    if kind == "double":
        return float(value)
    if kind == "int":
        return int(float(value))
    if kind == "boolean":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1")
    return value


def check_batch(source, raw_rows, ctx, mask_sample=None):
    """ctx gives lookups the rules need: known_claims, pan_owner, policy_end (from master data)."""
    spec = contract(source)
    total = len(raw_rows)
    findings, bad, good = [], [], []
    step_bronze, step_silver = lineage.bronze_node(source), lineage.silver_node(source)
    mask_sample = mask_sample or (lambda r: r)

    # 1. Schema: compare the columns that arrived with the contract.
    present = []
    for r in raw_rows:
        for k in r:
            if k not in present:
                present.append(k)
    missing = [c for c in spec if c not in present]
    extra = [c for c in present if c not in spec]
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("new " + ", ".join(extra))
        findings.append(Finding(source, "schema", step_bronze,
                                f"Columns changed: {'; '.join(parts)}", SEVERITY["schema"],
                                [mask_sample(r) for r in raw_rows[:2]],
                                {"missing": missing, "extra": extra}))
    blocking_missing = [c for c in missing if not spec[c].get("nullable", True)]
    if blocking_missing:  # can't build Silver rows without required columns
        return [], [(r, "schema", f"missing column {', '.join(blocking_missing)}") for r in raw_rows], findings

    # 2. Row rules from the contract.
    failures = {}  # check_name -> list of (row, reason)
    typed_rows = []
    for raw in raw_rows:
        row, reasons = {}, []
        for col, rule in spec.items():
            try:
                val = _coerce(raw.get(col), rule["type"])
            except (TypeError, ValueError):
                reasons.append(("type", f"{col}={raw.get(col)!r} is not {rule['type']}"))
                val = None
            row[col] = val
            if val is None:
                if not rule.get("nullable", True) and not any(c == "type" and r.startswith(col) for c, r in reasons):
                    reasons.append(("nulls", f"{col} is empty"))
                continue
            if "min" in rule and val < rule["min"]:
                reasons.append(("range", f"{col}={val} below {rule['min']}"))
            if "max" in rule and val > rule["max"]:
                reasons.append(("range", f"{col}={val} above {rule['max']}"))
            if "allowed" in rule and val not in rule["allowed"]:
                reasons.append(("allowed_values", f"{col}={val!r} not allowed"))
            if "pattern" in rule and not re.match(rule["pattern"], str(val)):
                reasons.append(("format", f"{col}={str(val)[:4]}... wrong format"))
        reasons += _source_rules(source, row, ctx)
        typed_rows.append((raw, row, reasons))

    # 3. Duplicates inside the batch and against what is already loaded.
    if source == "customer_kyc":
        # Same PAN under a different customer_id = the same person registered twice.
        pan_owner = dict(ctx.get("pan_owner", {}))
        for raw, row, reasons in typed_rows:
            pan, cid = row.get("pan"), row.get("customer_id")
            if pan and pan in pan_owner and pan_owner[pan] != cid:
                reasons.append(("duplicates", f"PAN already registered to {pan_owner[pan]}"))
            elif pan:
                pan_owner[pan] = cid
    else:
        keys_seen = set()
        for raw, row, reasons in typed_rows:
            key = _dup_key(source, row)
            if key is None:
                continue
            if key in keys_seen:
                reasons.append(("duplicates", f"duplicate record for {key[0]}"))
            keys_seen.add(key)

    for raw, row, reasons in typed_rows:
        if reasons:
            check_name, reason = reasons[0]
            bad.append((raw, check_name, "; ".join(r for _, r in reasons)))
            for c, r in reasons:
                failures.setdefault(c, []).append((raw, r))
        else:
            good.append(row)

    for check_name, items in failures.items():
        examples = sorted({r for _, r in items})[:3]
        findings.append(Finding(
            source, check_name, step_silver,
            f"{len(items)} of {total} rows failed {check_name.replace('_', ' ')}: {'; '.join(examples)}",
            SEVERITY.get(check_name, "MEDIUM"), [mask_sample(r) for r, _ in items[:3]],
            {"failed_rows": len(items), "total_rows": total}))
    return good, bad, findings


def _dup_key(source, row):
    if source == "telematics":
        return (row.get("device_id"), row.get("timestamp"))
    if source == "claims":
        return (row.get("vehicle_id"), row.get("accident_date"), row.get("claim_type"), row.get("claim_amount"))
    return None


def _source_rules(source, row, ctx):
    out = []
    if source == "policy" and row.get("start_date") and row.get("end_date"):
        if row["end_date"] <= row["start_date"]:
            out.append(("rule", f"end_date {row['end_date']} before start_date {row['start_date']}"))
    if source == "claims" and row.get("policy_id") and row.get("accident_date"):
        end = ctx.get("policy_end", {}).get(row["policy_id"])
        if end and date.fromisoformat(row["accident_date"]) > end:
            out.append(("referential", f"policy {row['policy_id']} expired on {end.isoformat()}"))
    if source == "garage_bills":
        if None not in (row.get("parts_cost"), row.get("labour_cost"), row.get("total")):
            if abs(row["parts_cost"] + row["labour_cost"] - row["total"]) > 1:
                out.append(("reconciliation", f"total {row['total']} != parts + labour "
                                              f"{round(row['parts_cost'] + row['labour_cost'], 2)}"))
        if row.get("claim_id") and row["claim_id"] not in ctx.get("known_claims", set()):
            out.append(("referential", f"claim {row['claim_id']} does not exist"))
    return out


def scan_pii(customer_rows):
    """Rows in Silver that still carry readable personal data."""
    leaks = []
    for r in customer_rows:
        if not r.get("masked") or PHONE_RE.match(str(r.get("phone", ""))) or PAN_RE.match(str(r.get("pan", ""))):
            leaks.append(r)
    return leaks
