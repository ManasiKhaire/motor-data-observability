"""Source 4 - Garage repair bills: workshop invoices for claims that were raised.

Bills refer to real claim_ids, read from the claims files already written,
so the Claims -> Garage bills link works in joins and lineage.
"""
import json
from datetime import timedelta

from common.io import utc_now
from generators.base import BaseGenerator


class GarageBillsGenerator(BaseGenerator):
    name = "garage_bills"
    problems = {
        "total_mismatch": "total is not parts_cost + labour_cost on about 30% of rows",
        "orphan_claim": "Bills point to claim_ids that do not exist",
        "late": "Garage SFTP delayed: nothing arrives, then all held bills arrive at once",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = []          # bills held back while "late" is on
        self.billed = set()     # claims already billed once

    def _recent_claims(self, limit_files=30):
        folder = self.out_dir / "landing" / "claims"
        if not folder.exists():
            return []
        files = sorted((f for f in folder.glob("claims_*.json")), reverse=True)[:limit_files]
        claims = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get("claim_id") not in self.billed:
                        claims.append(row)
        return claims

    def make_batch(self, n, problem):
        rng, rows = self.rng, []
        claims = self._recent_claims()
        if not claims and problem != "orphan_claim":
            self.log("INFO", "No new claims to bill yet; start the claims generator first")
            return None

        for c in rng.sample(claims, min(n, len(claims))) if claims else []:
            self.billed.add(c["claim_id"])
            parts = round(rng.uniform(3_000, 120_000), 2)
            labour = round(parts * rng.uniform(0.15, 0.4), 2)
            rows.append({
                "bill_id": f"BIL{utc_now().strftime('%y%m%d%H%M%S')}{len(rows):03d}{rng.randint(0, 99):02d}",
                "claim_id": c["claim_id"], "garage_id": c.get("garage_id"),
                "parts_cost": parts, "labour_cost": labour, "total": round(parts + labour, 2),
                "invoice_date": (utc_now() - timedelta(hours=rng.randint(0, 6))).date().isoformat(),
            })

        if problem == "total_mismatch":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)) if rows else []:
                r["total"] = round(r["total"] * rng.uniform(1.3, 2.0), 2)
        elif problem == "orphan_claim":
            for i in range(max(2, n // 3)):
                rows.append({
                    "bill_id": f"BIL{utc_now().strftime('%y%m%d%H%M%S')}9{i:02d}",
                    "claim_id": f"CLM-UNKNOWN-{rng.randint(1000, 9999)}",
                    "garage_id": rng.choice(self.ref.garages)["garage_id"],
                    "parts_cost": 50_000.0, "labour_cost": 15_000.0, "total": 65_000.0,
                    "invoice_date": utc_now().date().isoformat(),
                })
        elif problem == "late":
            self.held += rows
            self.log("ERROR", "Garage SFTP delivery delayed: connection timed out", held=len(self.held),
                     component="garage-sftp")
            return None

        if self.held:  # problem cleared: release everything that was held
            self.log("WARN", "Garage SFTP recovered: delivering held bills late", released=len(self.held))
            rows, self.held = self.held + rows, []
        return rows
