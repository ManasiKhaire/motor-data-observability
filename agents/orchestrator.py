"""Orchestrator: moves each incident through its life, one step per pipeline cycle.

  NEW alerts            -> group into an incident per source            (orchestrator)
  INVESTIGATING         -> root cause, impact, fix proposal, notify     (agents)
  AWAITING_APPROVAL     -> waits for a person (dashboard or approve.py)
  APPROVED              -> test on a sample -> apply -> verify -> report -> CLOSED
  REJECTED              -> MANUAL: owner fixes it; closes when its alerts clear
"""
import json

from agents import actions, fixer, impact, reporter, root_cause
from agents.notifier import notify
from pipeline.alerts import resolve_incident_alerts
from pipeline.db import one, rows, to_json
from pipeline.transform import now_iso

SEV_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
ACTIVE = ("INVESTIGATING", "AWAITING_APPROVAL", "APPROVED", "MANUAL", "FIX_FAILED")


def event(conn, incident_id, actor, message):
    conn.execute("INSERT INTO incident_events(incident_id, ts, actor, message) VALUES (?, ?, ?, ?)",
                 (incident_id, now_iso(), actor, message))


def decide(conn, incident_id, approve: bool, who: str):
    """Human decision, called by the dashboard or approve.py."""
    inc = one(conn, "SELECT status FROM incidents WHERE incident_id=?", (incident_id,))
    if not inc or inc["status"] != "AWAITING_APPROVAL":
        return False
    status = "APPROVED" if approve else "REJECTED"
    with conn:
        conn.execute("UPDATE incidents SET status=?, decided_by=?, decided_at=? WHERE incident_id=?",
                     (status, who, now_iso(), incident_id))
        event(conn, incident_id, who, "Approved the fix" if approve else "Rejected the fix; will fix manually")
    return True


class Agents:
    def __init__(self, conn, settings, out_dir, llm):
        self.conn, self.settings, self.out_dir, self.llm = conn, settings, out_dir, llm

    def step(self):
        with self.conn:
            self.group_alerts()
        for inc in rows(self.conn, "SELECT * FROM incidents WHERE status='INVESTIGATING'"):
            self.investigate(inc)
        for inc in rows(self.conn, "SELECT * FROM incidents WHERE status='APPROVED'"):
            self.execute(inc)
        with self.conn:
            self.close_manual()

    # 1. group new alerts
    def group_alerts(self):
        conn = self.conn
        for a in rows(conn, "SELECT * FROM alerts WHERE status='OPEN' AND incident_id IS NULL ORDER BY first_seen"):
            inc = one(conn, f"SELECT * FROM incidents WHERE source=? AND status IN {ACTIVE} "
                            "ORDER BY opened_at DESC LIMIT 1", (a["source"],))
            if inc:
                conn.execute("UPDATE alerts SET incident_id=? WHERE alert_id=?", (inc["incident_id"], a["alert_id"]))
                if SEV_RANK[a["severity"]] > SEV_RANK[inc["severity"]]:
                    conn.execute("UPDATE incidents SET severity=? WHERE incident_id=?",
                                 (a["severity"], inc["incident_id"]))
                event(conn, inc["incident_id"], "orchestrator", f"Linked new alert: {a['problem'][:120]}")
                continue
            n = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] + 1
            incident_id = f"INC-{n:04d}"
            title = f"{a['source']}: {a['problem'][:90]}"
            conn.execute("INSERT INTO incidents(incident_id, source, title, status, severity, opened_at) "
                         "VALUES (?, ?, ?, 'INVESTIGATING', ?, ?)",
                         (incident_id, a["source"], title, a["severity"], now_iso()))
            conn.execute("UPDATE alerts SET incident_id=? WHERE alert_id=?", (incident_id, a["alert_id"]))
            event(conn, incident_id, "orchestrator", f"Opened incident from alert {a['alert_id']} ({a['check_name']})")

    # 2. investigate
    def investigate(self, inc):
        conn, iid = self.conn, inc["incident_id"]
        alerts = rows(conn, "SELECT * FROM alerts WHERE incident_id=?", (iid,))
        root, _ = root_cause.analyze(self.llm, self.out_dir, inc, alerts)
        imp = impact.assess(conn, alerts)
        action, params, fix_text = fixer.propose(inc, alerts, root)
        with conn:
            conn.execute("""UPDATE incidents SET status='AWAITING_APPROVAL', root_cause=?, category=?, confidence=?,
                            evidence=?, impact=?, proposed_fix=?, fix_action=?, fix_params=?, llm_used=?
                            WHERE incident_id=?""",
                         (root["root_cause"], root["category"], root["confidence"], to_json(root["evidence"]),
                          to_json(imp), fix_text, action, to_json(params), int(root["by"] == "gemini"), iid))
            who = "root-cause agent (Gemini)" if root["by"] == "gemini" else "root-cause agent (rules)"
            event(conn, iid, who, f"{root['root_cause']} [{root['category']}, {root['confidence']:.0%}]")
            event(conn, iid, "impact agent", imp["summary"])
            event(conn, iid, "fix agent", f"Proposed: {fix_text.splitlines()[0]}")
            event(conn, iid, "notifier", "Owner notified; waiting for approval")
        notify(self.out_dir, iid, inc["title"],
               f"Root cause: {root['root_cause']}\nImpact: {imp['summary']}\nProposed fix: {fix_text}\n"
               f"Approve in the dashboard or run: python approve.py {iid}")

    # 3. test, apply, verify, report
    def execute(self, inc):
        conn, iid = self.conn, inc["incident_id"]
        params = json.loads(inc["fix_params"] or "{}")
        passed, msg = actions.test(conn, self.out_dir, inc["fix_action"], params)
        with conn:
            event(conn, iid, "fix agent", f"Test on sample: {msg}")
            if not passed:
                conn.execute("UPDATE incidents SET status='FIX_FAILED', test_result=? WHERE incident_id=?", (msg, iid))
                event(conn, iid, "fix agent", "Test failed; real pipeline left unchanged. Needs manual work.")
                return
            applied = actions.apply(conn, self.out_dir, inc["fix_action"], params)
            ok, check = actions.verify(conn, inc["fix_action"], params)
            event(conn, iid, "fix agent", f"Applied: {applied}")
            event(conn, iid, "fix agent", f"Verified: {check}")
            resolve_incident_alerts(conn, iid)
            conn.execute("UPDATE incidents SET status='CLOSED', test_result=?, closed_at=? WHERE incident_id=?",
                         (f"{msg} {check}", now_iso(), iid))
            event(conn, iid, "orchestrator", "Incident closed")
        self._report(iid)

    def close_manual(self):
        for inc in rows(self.conn, "SELECT * FROM incidents WHERE status IN ('REJECTED', 'MANUAL')"):
            if inc["status"] == "REJECTED":
                self.conn.execute("UPDATE incidents SET status='MANUAL' WHERE incident_id=?", (inc["incident_id"],))
                continue
            open_left = self.conn.execute("SELECT COUNT(*) FROM alerts WHERE incident_id=? AND status='OPEN'",
                                          (inc["incident_id"],)).fetchone()[0]
            if open_left == 0:
                self.conn.execute("UPDATE incidents SET status='CLOSED', closed_at=?, test_result=? "
                                  "WHERE incident_id=?", (now_iso(), "Fixed manually by owner", inc["incident_id"]))
                event(self.conn, inc["incident_id"], "orchestrator", "All alerts cleared; closed as fixed manually")
                self._report(inc["incident_id"])

    def _report(self, iid):
        inc = one(self.conn, "SELECT * FROM incidents WHERE incident_id=?", (iid,))
        events = rows(self.conn, "SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (iid,))
        text = reporter.write(self.llm, inc, events)
        with self.conn:
            self.conn.execute("UPDATE incidents SET report=? WHERE incident_id=?", (text, iid))
        path = self.out_dir / "reports"
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{iid}.md").write_text(text, encoding="utf-8")
