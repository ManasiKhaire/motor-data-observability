"""Root cause agent: gathers evidence, then asks Gemini (or its own rules) why it broke.

Evidence = the incident's alerts + WARN/ERROR log lines from the source and the pipeline
around the time it started + what sits upstream in the lineage.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from pipeline import lineage

CATEGORIES = {
    "expired_credential": "A secret or token expired, so a pipeline step could not do its job",
    "schema_change": "The source changed its columns (renamed, added or removed)",
    "source_outage": "The source stopped sending data (connector or broker down)",
    "delayed_delivery": "The source is sending data late (delivery delayed)",
    "bad_source_values": "The source sent invalid values (empty, out of range, wrong format, sums wrong)",
    "duplicate_records": "The source sent the same records more than once",
    "referential_break": "Records point to things that do not exist or are no longer valid",
}

SYSTEM = (
    "You are the root cause agent of a data observability platform for a motor insurance data platform. "
    "Use ONLY the evidence given. Answer in JSON with keys: root_cause (one sentence a manager understands), "
    "category (exactly one of the given categories), confidence (0 to 1), evidence (2 to 4 short strings "
    "quoting the alerts or log lines you relied on), recommended_fix (one or two sentences)."
)


def read_logs(out_dir, source, since, limit=12):
    lines = []
    for name in (source, "pipeline"):
        path = Path(out_dir) / "logs" / f"{name}.log"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines()[-400:]:
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if name == "pipeline" and e.get("related_source") != source:
                continue  # shared pipeline log: keep only lines about this source
            if e.get("level") in ("WARN", "ERROR") and e.get("ts", "") >= since and "Unknown problem" not in e.get("message", ""):
                lines.append({"ts": e["ts"][:19], "log": name, "level": e["level"], "message": e["message"]})
    seen, unique = set(), []
    for l in sorted(lines, key=lambda x: x["ts"]):
        if l["message"] not in seen:
            seen.add(l["message"])
            unique.append(l)
    return unique[-limit:]


def gather_evidence(out_dir, incident, alerts):
    first = min(a["first_seen"] for a in alerts)
    since = (datetime.fromisoformat(first) - timedelta(minutes=15)).isoformat()
    steps = sorted({a["step"] for a in alerts})
    return {
        "source": incident["source"],
        "alerts": [{"check": a["check_name"], "step": a["step"], "severity": a["severity"],
                    "problem": a["problem"], "first_seen": a["first_seen"][:19], "occurrences": a["occurrences"]}
                   for a in alerts],
        "logs": read_logs(out_dir, incident["source"], since),
        "upstream_of_failing_steps": {s: lineage.upstream(s) for s in steps},
        "secrets_used": {n: d["depends_on_secret"] for n, d in lineage.nodes().items() if "depends_on_secret" in d},
    }


def rule_based(evidence):
    """Deterministic answer used when Gemini is off or fails. Also a sanity check for the LLM."""
    checks = {a["check"] for a in evidence["alerts"]}
    text = " ".join(l["message"].lower() for l in evidence["logs"])
    # The earliest error is usually the cause; later ones are symptoms.
    errors = [l for l in evidence["logs"] if l["level"] == "ERROR"] or evidence["logs"]
    best_log = errors[0]["message"] if errors else None
    src = evidence["source"]

    if "pii" in checks:
        cat = "expired_credential" if ("403" in text or "token" in text) else "bad_source_values"
        cause = ("The Vault token used by the PII masking step expired (Vault returned 403), so masking was "
                 "skipped and raw personal data reached silver_customer." if cat == "expired_credential"
                 else "The PII masking step failed, so raw personal data reached silver_customer.")
    elif "schema" in checks:
        cat = "schema_change"
        detail = next((a["problem"] for a in evidence["alerts"] if a["check"] == "schema"), "")
        cause = f"The {src} source changed its columns ({detail.replace('Columns changed: ', '')})"
        cause += f", after: {best_log}." if best_log else "."
    elif "freshness" in checks:
        cat = "delayed_delivery" if ("delay" in text or "timed out" in text) else "source_outage"
        cause = f"The {src} feed stopped delivering files" + (f": {best_log}." if best_log else ".")
    elif "duplicates" in checks:
        cat, cause = "duplicate_records", f"The {src} source is sending duplicate records."
    elif "referential" in checks:
        cat, cause = "referential_break", f"The {src} source sent records that point to missing or expired items."
    else:
        cat = "bad_source_values"
        cause = f"The {src} source is sending invalid values" + (f" (possible trigger: {best_log})." if best_log else ".")
    return {"root_cause": cause, "category": cat, "confidence": 0.7 if best_log else 0.55,
            "evidence": [a["problem"][:160] for a in evidence["alerts"][:2]] + ([best_log] if best_log else []),
            "recommended_fix": None, "by": "rules"}


def analyze(llm, out_dir, incident, alerts):
    evidence = gather_evidence(out_dir, incident, alerts)
    fallback = rule_based(evidence)
    prompt = ("Categories:\n" + json.dumps(CATEGORIES, indent=1) +
              "\n\nEvidence:\n" + json.dumps(evidence, indent=1) +
              "\n\nWhat is the most likely root cause?")
    answer = llm.generate_json(SYSTEM, prompt) if llm else None
    if answer and answer.get("category") in CATEGORIES and answer.get("root_cause"):
        try:
            confidence = float(answer.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        return {"root_cause": str(answer["root_cause"]), "category": answer["category"],
                "confidence": max(0.0, min(1.0, confidence)),
                "evidence": [str(e) for e in answer.get("evidence", [])][:4] or fallback["evidence"],
                "recommended_fix": answer.get("recommended_fix"), "by": "gemini",
                "rules_agree": answer["category"] == fallback["category"]}, evidence
    return fallback, evidence
