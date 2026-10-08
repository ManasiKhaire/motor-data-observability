"""Source 4 - Claims (FNOL, first notice of loss). Owner: Person 4

Accidents reported by customers.

Fields to produce, one dict per claim:
  claim_id                      unique, e.g. CLM + timestamp + counter
  policy_id, vehicle_id         from self.ref.active_policies
  accident_date, reported_at, location, claim_type, claim_amount, status, garage_id
  (garage_id from self.ref.garages, ideally in the customer's city)

claim_type: Collision | Theft | Windshield | Flood | Third Party
Rules: amount above zero, one claim per vehicle per day, policy active on accident_date.
Garage bills (Person 5) read claim_ids from your files, so keep claim_id unique.
"""
from generators.base import BaseGenerator


class ClaimsGenerator(BaseGenerator):
    name = "claims"
    problems = {
        "null_amount": "claim_amount empty on some rows",
        "negative_amount": "claim_amount below zero on some rows",
        "duplicate_claim": "Same vehicle claimed twice on the same day under a new claim_id",
        "expired_policy": "Claims raised against policies that have already expired",
    }

    def make_batch(self, n, problem):
        # TODO 1: build n claims from self.ref.active_policies
        #         (use self.ref.expired_policies when problem == "expired_policy")
        # TODO 2: apply the other problems when set, and self.log() a cause
        # TODO 3: return the list of dicts
        raise NotImplementedError("Person 4: write the claims generator")
