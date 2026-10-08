"""Source 2 - Policy & vehicle: new policies, renewals and changes from the policy admin system."""
from datetime import date, timedelta

from generators.base import BaseGenerator


class PolicyGenerator(BaseGenerator):
    name = "policy"
    problems = {
        "schema_drift": "Column vehicle_id renamed to vehicle_ref, new column sales_channel added",
        "bad_dates": "end_date earlier than start_date on about 30% of rows",
        "missing_vehicle_id": "vehicle_id empty on about 30% of rows (breaks joins)",
    }

    def make_batch(self, n, problem):
        rng, rows = self.rng, []
        for p in rng.sample(self.ref.policies, min(n, len(self.ref.policies))):
            rows.append({
                "policy_id": p["policy_id"], "customer_id": p["customer_id"], "vehicle_id": p["vehicle_id"],
                "reg_no": p["reg_no"], "make": p["make"], "model": p["model"], "year": p["year"],
                "cover_type": p["cover_type"], "sum_insured": p["sum_insured"], "premium": p["premium"],
                "start_date": p["start_date"].isoformat(), "end_date": p["end_date"].isoformat(),
                "change_type": rng.choice(["NEW", "RENEWAL", "ENDORSEMENT"]),
            })

        if problem == "schema_drift":
            for r in rows:
                r["vehicle_ref"] = r.pop("vehicle_id")
                r["sales_channel"] = rng.choice(["AGENT", "ONLINE", "BANCA"])
            self.log("WARN", "Policy admin release v9 deployed: field vehicle_id renamed to vehicle_ref",
                     component="policy-admin")
        elif problem == "bad_dates":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)):
                start = date.fromisoformat(r["start_date"])
                r["end_date"] = (start - timedelta(days=rng.randint(1, 200))).isoformat()
        elif problem == "missing_vehicle_id":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)):
                r["vehicle_id"] = None
        return rows
