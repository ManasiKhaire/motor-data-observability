"""Source 3 - Customer / driver KYC. Owner: Person 3

Personal details of policyholders. Like a real source system, send RAW personal data;
masking is the pipeline's job (Bronze -> Silver).

Fields to produce, one dict per customer update:
  customer_id, name, phone, email, licence_no, pan, address, dob, city   from self.ref.customers
  updated_at                                                             common.io.utc_now()

The expired_token problem drives the main demo story:
  call common.io.set_vault_token(self.out_dir, "expired") and log
  "Vault returned 403 on token refresh"; set it back to "valid" when the problem is off.
  The pipeline's masking step reads output/control/vault_token.json and fails if expired.
"""
from generators.base import BaseGenerator


class CustomerKycGenerator(BaseGenerator):
    name = "customer_kyc"
    problems = {
        "expired_token": "Vault token expires: the masking step can no longer mask PII",
        "duplicate_customer": "Same person sent again under a new customer_id",
        "invalid_pan": "PAN numbers in the wrong format on some rows",
    }

    def make_batch(self, n, problem):
        # TODO 1: keep the vault token file in sync with `problem` (see docstring)
        # TODO 2: pick n customers from self.ref.customers and turn each into a row
        # TODO 3: apply duplicate_customer / invalid_pan when set
        # TODO 4: return the list of dicts
        raise NotImplementedError("Person 3: write the customer KYC generator")
