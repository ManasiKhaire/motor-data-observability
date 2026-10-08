"""Source 5 - Garage repair bills. Owner: Person 5

Workshop invoices for claims that were raised.

Fields to produce, one dict per bill:
  bill_id                                    unique
  claim_id, garage_id                        read from the claims files in
                                             self.out_dir / "landing" / "claims" (JSON lines)
  parts_cost, labour_cost, total, invoice_date

Rules: total == parts_cost + labour_cost, claim_id must exist in claims.
Written as CSV. Needs the claims generator running first.

The late problem: hold bills back (return None) while it is on, then release all
held bills in one batch when it is switched off.
"""
from generators.base import BaseGenerator


class GarageBillsGenerator(BaseGenerator):
    name = "garage_bills"
    problems = {
        "total_mismatch": "total is not parts_cost + labour_cost on some rows",
        "orphan_claim": "Bills point to claim_ids that do not exist",
        "late": "Garage SFTP delayed: nothing arrives, then all held bills arrive at once",
    }

    def make_batch(self, n, problem):
        # TODO 1: read recent claim_ids from the claims landing folder
        # TODO 2: build up to n bills for claims not billed yet
        # TODO 3: apply `problem` when set, and self.log() a realistic cause
        # TODO 4: return the list of dicts (or None when there is nothing to send)
        raise NotImplementedError("Person 5: write the garage bills generator")
