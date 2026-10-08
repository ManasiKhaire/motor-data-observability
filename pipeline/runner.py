"""One pipeline cycle: ingest new files -> check -> Silver -> PII scan -> Gold -> node status -> agents.

run_cycle() is called every few seconds by run_pipeline.py.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from common.io import log_event
from common.reference import build_reference
from pipeline import checks, gold, lineage
from pipeline.alerts import note_clean_batch, raise_alert, resolve
from pipeline.db import rows, to_json
from pipeline.transform import apply_mappings, get_mappings, mask_row, now_iso, to_silver

# Claims before garage bills, so bills can see the claims they refer to.
SOURCES = ["telematics", "policy", "claims", "garage_bills", "customer_kyc"]


class Pipeline:
    def __init__(self, conn, settings, out_dir):
        self.conn, self.settings, self.out_dir = conn, settings, Path(out_dir)
        ref = build_reference(settings)
        self.policy_end = {p["policy_id"]: p["end_date"] for p in ref.policies}
        self.started = datetime.now(timezone.utc)

    # ---------- reading files ----------
    def new_files(self, source):
        folder = self.out_dir / "landing" / source
        if not folder.exists():
            return []
        done = {r[0] for r in self.conn.execute("SELECT file FROM file_registry WHERE source=?", (source,))}
        return sorted(f for f in folder.iterdir() if not f.name.startswith("_") and f.name not in done)

    @staticmethod
    def read_file(path):
        if path.suffix == ".json":
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    # ---------- one file ----------
    def process_file(self, source, path):
        raw = self.read_file(path)
        conn, ts = self.conn, now_iso()
        conn.executemany("INSERT INTO bronze(source, batch_file, payload, ingested_at) VALUES (?, ?, ?, ?)",
                         [(source, path.name, to_json(r), ts) for r in raw])
        mapped = apply_mappings(raw, get_mappings(conn, source))

        ctx = {"policy_end": self.policy_end}
        if source == "garage_bills":
            ctx["known_claims"] = {r[0] for r in conn.execute("SELECT claim_id FROM silver_claims")} | {
                r[0] for r in conn.execute(
                    "SELECT json_extract(payload, '$.claim_id') FROM bronze WHERE source='claims'")}
        if source == "customer_kyc":
            ctx["pan_owner"] = {}
            for pan, cid in conn.execute(
                    "SELECT json_extract(payload,'$.pan'), json_extract(payload,'$.customer_id') FROM bronze "
                    "WHERE source='customer_kyc' AND batch_file<>? ORDER BY id", (path.name,)):
                ctx["pan_owner"].setdefault(pan, cid)

        masker = mask_row if source == "customer_kyc" else None
        good, bad, findings = checks.check_batch(source, mapped, ctx, masker)
        conn.executemany(
            "INSERT INTO quarantine(source, batch_file, check_name, reason, payload, quarantined_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(source, path.name, c, reason, to_json(mask_row(r) if masker else r), ts) for r, c, reason in bad])
        to_silver(conn, self.out_dir, source, good, path.name)

        new_alerts = sum(raise_alert(conn, f) for f in findings)
        note_clean_batch(conn, source, {f.check_name for f in findings},
                         self.settings["pipeline"]["resolve_after_clean_batches"])
        conn.execute("INSERT INTO file_registry(file, source, rows, ingested_at) VALUES (?, ?, ?, ?)",
                     (path.name, source, len(raw), ts))
        return len(raw), new_alerts

    # ---------- checks across files ----------
    def check_freshness(self):
        factor = self.settings["pipeline"]["freshness_factor"]
        new = 0
        for source in SOURCES:
            last = self.conn.execute(
                "SELECT MAX(ingested_at) FROM file_registry WHERE source=?", (source,)).fetchone()[0]
            if last is None:
                continue  # source never started; nothing to compare against
            limit = self.settings["sources"][source]["interval_seconds"] * factor
            silent = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if silent > limit:
                new += raise_alert(self.conn, checks.Finding(
                    source, "freshness", lineage.source_node(source),
                    f"No new {source} data for {int(silent)}s (expected every "
                    f"{self.settings['sources'][source]['interval_seconds']}s)", "HIGH", [],
                    {"silent_seconds": int(silent), "limit_seconds": limit}))
            else:
                resolve(self.conn, source, "freshness")
        return new

    def check_pii(self):
        leaks = checks.scan_pii(rows(self.conn, "SELECT * FROM silver_customer"))
        if leaks:
            new = raise_alert(self.conn, checks.Finding(
                "customer_kyc", "pii", "mask_pii",
                f"{len(leaks)} customer rows in Silver contain unmasked personal data (phone, PAN, licence)",
                "CRITICAL", [mask_row(l) for l in leaks[:3]], {"unmasked_rows": len(leaks)}))
            for node in ("gold_customer_360", "gold_compliance_report"):
                gold.block(self.conn, node, "Unmasked PII in silver_customer")
            if new:
                log_event(self.out_dir, "pipeline", "ERROR",
                          "Containment: publishing of gold_customer_360 and gold_compliance_report halted "
                          "because silver_customer holds unmasked PII", component="containment",
                          related_source="customer_kyc")
            return int(new)
        resolve(self.conn, "customer_kyc", "pii")
        for node in ("gold_customer_360", "gold_compliance_report"):
            r = self.conn.execute("SELECT reason FROM gold_refresh WHERE node=?", (node,)).fetchone()
            if r and r[0] == "Unmasked PII in silver_customer":
                gold.unblock(self.conn, node)
        return 0

    # ---------- map colours ----------
    def update_node_status(self):
        conn = self.conn
        status = {n: ("green", "") for n in lineage.nodes()}
        rank = {"green": 0, "yellow": 1, "red": 2}
        sev_colour = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "yellow"}
        for a in rows(conn, "SELECT step, severity, problem FROM alerts WHERE status='OPEN'"):
            colour = sev_colour.get(a["severity"], "yellow")
            if a["step"] in status and rank[colour] >= rank[status[a["step"]][0]]:
                status[a["step"]] = (colour, a["problem"])
        for g in rows(conn, "SELECT node, reason FROM gold_refresh WHERE blocked=1"):
            status[g["node"]] = ("red", f"Publishing blocked: {g['reason']}")
        for node, (colour, _) in list(status.items()):
            if colour == "red":
                for d in lineage.downstream(node):
                    if status[d][0] == "green":
                        status[d] = ("yellow", f"At risk: upstream {node} has a problem")
        conn.execute("DELETE FROM node_status")
        conn.executemany("INSERT INTO node_status(node, status, detail, updated_at) VALUES (?, ?, ?, ?)",
                         [(n, c, d, now_iso()) for n, (c, d) in status.items()])

    # ---------- the cycle ----------
    def run_cycle(self, agents=None):
        started = now_iso()
        files = total_rows = new_alerts = 0
        with self.conn:
            for source in SOURCES:
                for path in self.new_files(source):
                    n, a = self.process_file(source, path)
                    files, total_rows, new_alerts = files + 1, total_rows + n, new_alerts + a
            new_alerts += self.check_freshness()
            new_alerts += self.check_pii()
            gold.refresh(self.conn)
            self.update_node_status()
            self.conn.execute("INSERT INTO pipeline_runs(started_at, finished_at, files, rows, new_alerts) "
                              "VALUES (?, ?, ?, ?, ?)", (started, now_iso(), files, total_rows, new_alerts))
        if agents is not None:
            agents.step()
            with self.conn:
                gold.refresh(self.conn)
                self.update_node_status()
        return {"files": files, "rows": total_rows, "new_alerts": new_alerts}
