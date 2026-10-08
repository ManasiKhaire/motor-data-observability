"""Source 3 - Claims (FNOL, first notice of loss): accidents reported by customers."""
from datetime import date, timedelta

from common.io import utc_now
from generators.base import BaseGenerator

CLAIM_TYPES = {  # type: (min amount, max amount) in INR
    "Collision": (15_000, 300_000), "Theft": (200_000, 900_000), "Windshield": (5_000, 25_000),
    "Flood": (40_000, 400_000), "Third Party": (20_000, 500_000),
}


class ClaimsGenerator(BaseGenerator):
    name = "claims"
    problems = {
        "null_amount": "claim_amount empty on about 30% of rows",
        "negative_amount": "claim_amount below zero on about 30% of rows",
        "duplicate_claim": "Same vehicle claimed twice on the same day under a new claim_id",
        "expired_policy": "Claims raised against policies that have already expired",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = 0

    def _claim_id(self):
        self.counter += 1
        return f"CLM{utc_now().strftime('%y%m%d%H%M%S')}{self.counter:05d}"

    def make_batch(self, n, problem):
        rng, rows = self.rng, []
        pool = self.ref.expired_policies if problem == "expired_policy" else self.ref.active_policies
        cities = {c["customer_id"]: c["city"] for c in self.ref.customers}

        for p in rng.choices(pool, k=n):
            claim_type = rng.choice(list(CLAIM_TYPES))
            low, high = CLAIM_TYPES[claim_type]
            city = cities[p["customer_id"]]
            garages = [g for g in self.ref.garages if g["city"] == city] or self.ref.garages
            accident = date.today() - timedelta(days=rng.randint(0, 5))
            if problem == "expired_policy":
                accident = p["end_date"] + timedelta(days=rng.randint(5, 60))
            rows.append({
                "claim_id": self._claim_id(), "policy_id": p["policy_id"], "vehicle_id": p["vehicle_id"],
                "accident_date": accident.isoformat(), "reported_at": utc_now().isoformat(),
                "location": city, "claim_type": claim_type,
                "claim_amount": round(rng.uniform(low, high), 2), "status": "FNOL",
                "garage_id": rng.choice(garages)["garage_id"],
            })

        if problem == "null_amount":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)):
                r["claim_amount"] = None
        elif problem == "negative_amount":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)):
                r["claim_amount"] = -abs(r["claim_amount"])
        elif problem == "duplicate_claim":
            for r in rng.sample(rows, max(1, len(rows) // 4)):
                twin = dict(r, claim_id=self._claim_id())
                rows.append(twin)
        return rows
