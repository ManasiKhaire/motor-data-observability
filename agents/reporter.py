"""Report agent: writes the closing incident report (Gemini summary, with a rule-based fallback)."""
import json
from datetime import datetime

SYSTEM = ("You write short incident reports for a data platform team. Answer in JSON with keys: "
          "summary (3 to 4 plain sentences: what broke, why, impact, how it was fixed) and "
          "prevention (list of 2 or 3 short actions to stop it happening again). Use only the facts given.")


PREVENTION = {
    "expired_credential": ["Alert 7 days before any pipeline token expires",
                           "Make masking fail closed: stop the load instead of passing raw data"],
    "schema_change": ["Agree column changes with the source team before release",
                      "Keep the schema contract in version control and review changes"],
    "source_outage": ["Add a heartbeat check on the connector", "Restart the connector automatically once"],
    "delayed_delivery": ["Agree a delivery SLA with the source", "Alert the source team when files run late"],
    "bad_source_values": ["Share the failing rules with the source team",
                          "Add the same validation at the source system"],
    "duplicate_records": ["Add an idempotency key at the source", "Deduplicate on ingest"],
    "referential_break": ["Validate references at the source before sending",
                          "Load reference data before dependent feeds"],
}


def _minutes(a, b):
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60, 1)


def write(llm, incident, events):
    impact = json.loads(incident["impact"] or "{}")
    evidence = json.loads(incident["evidence"] or "[]")
    mttr = _minutes(incident["opened_at"], incident["closed_at"])
    facts = {
        "incident": incident["incident_id"], "title": incident["title"], "root_cause": incident["root_cause"],
        "impact": impact.get("summary"), "fix": incident["proposed_fix"], "test": incident["test_result"],
        "decided_by": incident["decided_by"], "minutes_to_resolve": mttr,
    }
    answer = llm.generate_json(SYSTEM, json.dumps(facts, indent=1)) if llm else None
    if answer and answer.get("summary"):
        summary, prevention = answer["summary"], answer.get("prevention") or []
    else:
        summary = (f"{incident['title']}. Root cause: {incident['root_cause']} "
                   f"Impact: {impact.get('summary', 'n/a')} Fixed by: {(incident['proposed_fix'] or 'n/a').splitlines()[0]} "
                   f"Resolved in {mttr} minutes after approval by {incident['decided_by']}.")
        prevention = PREVENTION.get(incident["category"], PREVENTION["bad_source_values"])

    lines = [f"# Incident report {incident['incident_id']}", "",
             f"**{incident['title']}**", "",
             f"- Severity: {incident['severity']}",
             f"- Opened: {incident['opened_at'][:19]} UTC",
             f"- Closed: {incident['closed_at'][:19]} UTC ({mttr} min)",
             f"- Approved by: {incident['decided_by']}", "",
             "## Summary", "", summary, "",
             "## Root cause", "", f"{incident['root_cause']} (confidence {(incident['confidence'] or 0):.0%})", ""]
    if evidence:
        lines += ["Evidence:", ""] + [f"- {e}" for e in evidence] + [""]
    lines += ["## Impact", "", impact.get("summary", "n/a"), "",
              "## Fix", "", incident["proposed_fix"] or "n/a", "", f"Test: {incident['test_result']}", "",
              "## Prevention", ""] + [f"- {p}" for p in prevention] + ["",
              "## Timeline", ""] + [f"- {e['ts'][11:19]} {e['actor']}: {e['message']}" for e in events]
    return "\n".join(lines)
