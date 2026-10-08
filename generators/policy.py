"""Source 2 - Policy & vehicle. Owner: Person 2

New policies, renewals and changes from the policy admin system.

Fields to produce, one dict per policy event:
  policy_id, customer_id, vehicle_id, reg_no, make, model, year   from self.ref.policies
  cover_type, sum_insured, premium, start_date, end_date          from self.ref.policies
  change_type                                                     NEW | RENEWAL | ENDORSEMENT

Rules (see schemas/source_contracts.json): end_date after start_date, no empty IDs.
Written as CSV.
"""
from generators.base import BaseGenerator


class PolicyGenerator(BaseGenerator):
    name = "policy"
    problems = {
        "schema_drift": "Column vehicle_id renamed to vehicle_ref, new column added",
        "bad_dates": "end_date earlier than start_date on some rows",
        "missing_vehicle_id": "vehicle_id empty on some rows (breaks joins)",
    }

    def make_batch(self, n, problem):
        # TODO 1: pick n policies from self.ref.policies and turn each into a row
        # TODO 2: apply `problem` when it is set, and self.log() a realistic cause
        #         (e.g. "Policy admin release v9 deployed")
        # TODO 3: return the list of dicts
        raise NotImplementedError("Person 2: write the policy generator")
