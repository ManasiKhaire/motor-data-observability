"""Source 5 - Customer / driver KYC: personal details of policyholders.

Like real source systems, this feed sends RAW personal data (name, phone,
PAN, licence). Masking is the pipeline's job (Bronze -> Silver), and the
masking step needs a valid Vault token. The "expired_token" problem marks
the token as expired in output/control/vault_token.json and logs the 403,
so the masking step fails and unmasked PII leaks downstream: the main demo story.
"""
from common.io import set_vault_token, utc_now
from generators.base import BaseGenerator


class CustomerKycGenerator(BaseGenerator):
    name = "customer_kyc"
    problems = {
        "expired_token": "Vault token expires: the masking step can no longer mask PII",
        "duplicate_customer": "Same person sent again under a new customer_id",
        "invalid_pan": "PAN numbers in the wrong format on about 30% of rows",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token_status = None
        self.dup_counter = 900_000

    def _sync_token(self, problem):
        status = "expired" if problem == "expired_token" else "valid"
        if status != self.token_status:
            set_vault_token(self.out_dir, status)
            if status == "expired":
                self.log("ERROR", "Vault returned 403 on token refresh", component="vault",
                         http_status=403)
            elif self.token_status == "expired":
                self.log("INFO", "Vault token renewed", component="vault")
            self.token_status = status

    def make_batch(self, n, problem):
        self._sync_token(problem)
        rng, rows = self.rng, []
        for c in rng.sample(self.ref.customers, min(n, len(self.ref.customers))):
            rows.append({
                "customer_id": c["customer_id"], "name": c["name"], "phone": c["phone"],
                "email": c["email"], "licence_no": c["licence_no"], "pan": c["pan"],
                "address": c["address"], "dob": c["dob"], "city": c["city"],
                "updated_at": utc_now().isoformat(),
            })

        if problem == "duplicate_customer":
            for r in rng.sample(rows, max(1, len(rows) // 4)):
                self.dup_counter += 1
                rows.append(dict(r, customer_id=f"CUST{self.dup_counter:06d}"))
        elif problem == "invalid_pan":
            for r in rng.sample(rows, max(1, len(rows) * 3 // 10)):
                r["pan"] = r["pan"][:4] + "-" + r["pan"][5:]
        return rows
