"""The shared alerts table: one open alert per (source, check), updated as problems repeat."""
import uuid

from pipeline.db import from_json, one, to_json
from pipeline.transform import now_iso


def raise_alert(conn, finding):
    """Open a new alert, or bump the open one for the same source and check. Returns True if new."""
    existing = one(conn, "SELECT * FROM alerts WHERE source=? AND check_name=? AND status='OPEN'",
                   (finding.source, finding.check_name))
    if existing:
        detail = from_json(existing["detail"], {})
        detail.update(finding.detail)
        conn.execute("""UPDATE alerts SET last_seen=?, occurrences=occurrences+1, clean_batches=0,
                        problem=?, sample=?, detail=? WHERE alert_id=?""",
                     (now_iso(), finding.problem, to_json(finding.sample), to_json(detail), existing["alert_id"]))
        return False
    conn.execute("""INSERT INTO alerts(alert_id, source, step, check_name, problem, severity, first_seen,
                    last_seen, occurrences, clean_batches, status, sample, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 'OPEN', ?, ?)""",
                 (f"ALT-{uuid.uuid4().hex[:8].upper()}", finding.source, finding.step, finding.check_name,
                  finding.problem, finding.severity, now_iso(), now_iso(), to_json(finding.sample),
                  to_json(finding.detail)))
    return True


def note_clean_batch(conn, source, failed_checks, resolve_after):
    """A batch passed some checks: count towards closing those open alerts."""
    for a in conn.execute("SELECT alert_id, check_name FROM alerts WHERE source=? AND status='OPEN' "
                          "AND check_name NOT IN ('freshness', 'pii')", (source,)).fetchall():
        if a["check_name"] in failed_checks:
            continue
        conn.execute("UPDATE alerts SET clean_batches=clean_batches+1 WHERE alert_id=?", (a["alert_id"],))
        conn.execute("UPDATE alerts SET status='RESOLVED' WHERE alert_id=? AND clean_batches>=?",
                     (a["alert_id"], resolve_after))


def resolve(conn, source, check_name):
    conn.execute("UPDATE alerts SET status='RESOLVED' WHERE source=? AND check_name=? AND status='OPEN'",
                 (source, check_name))


def resolve_incident_alerts(conn, incident_id):
    conn.execute("UPDATE alerts SET status='RESOLVED' WHERE incident_id=? AND status='OPEN'", (incident_id,))
