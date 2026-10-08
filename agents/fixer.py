"""Fix agent: turns a root cause category into ONE known, safe action.

The LLM may word the recommendation, but it never invents the action: code only runs
actions from this playbook, and only after a person approves.
"""
import difflib

from pipeline.db import from_json

PLAYBOOK = {
    "expired_credential": "renew_vault_token",
    "schema_change": "add_column_mapping",
    "source_outage": "restart_connector",
    "delayed_delivery": "restart_connector",
    "bad_source_values": "quarantine_and_notify_source",
    "duplicate_records": "quarantine_and_notify_source",
    "referential_break": "quarantine_and_notify_source",
}


def _mapping_from_alerts(alerts):
    """Pair renamed columns (missing ones with similar new ones); drop other new columns."""
    for a in alerts:
        if a["check_name"] != "schema":
            continue
        detail = from_json(a["detail"], {})
        missing, extra = list(detail.get("missing", [])), list(detail.get("extra", []))
        renames, used = {}, set()
        for m in missing:
            match = difflib.get_close_matches(m, [e for e in extra if e not in used], n=1, cutoff=0.5)
            if match:
                renames[match[0]] = m
                used.add(match[0])
        drops = [e for e in extra if e not in used]
        return {"rename": renames, "drop": drops}
    return {"rename": {}, "drop": []}


def propose(incident, alerts, root):
    category = root["category"]
    action = PLAYBOOK.get(category, "quarantine_and_notify_source")
    source = incident["source"]
    params = {"source": source}

    if action == "renew_vault_token":
        text = ("Renew the Vault token used by the PII masking step, re-mask every leaked row in "
                "silver_customer, then release gold_customer_360 and gold_compliance_report.")
    elif action == "add_column_mapping":
        params.update(_mapping_from_alerts(alerts))
        renames = ", ".join(f"{k} -> {v}" for k, v in params["rename"].items()) or "none found"
        drops = ", ".join(params["drop"]) or "none"
        text = (f"Add a column mapping for {source} (rename: {renames}; ignore new: {drops}), "
                "then reload the quarantined rows into Silver.")
    elif action == "restart_connector":
        text = f"Restart the {source} connector and confirm new files arrive within one interval."
    else:
        text = (f"Keep the bad {source} rows in quarantine (they never reached Silver) and ask the "
                f"{source} source owner to fix the feed; confirm the next batches pass.")

    if root.get("recommended_fix") and root.get("by") == "gemini":
        text = f"{text}\nGemini's view: {root['recommended_fix']}"
    return action, params, text
