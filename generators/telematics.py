"""Source 1 - Telematics (IoT). Owner: Person 1

Driving readings from devices fitted in insured cars.

Fields to produce, one dict per reading:
  device_id, vehicle_id, policy_id   take from self.ref.devices so IDs match other sources
  timestamp                          ISO string, use common.io.utc_now()
  speed_kmph, harsh_brake, harsh_accel, lat, lon, odometer_km

Rules your data should follow (see schemas/source_contracts.json):
  speed 0-220, lat 6-37, lon 68-98 (India), odometer only goes up
"""
from generators.base import BaseGenerator


class TelematicsGenerator(BaseGenerator):
    name = "telematics"
    problems = {
        "feed_stop": "Device broker goes down: no files arrive at all",
        "duplicates": "Same readings sent twice",
        "impossible_values": "Speeds of 400 km/h and GPS points outside the map",
    }

    def make_batch(self, n, problem):
        # TODO 1: build n readings from self.ref.devices (use self.rng for randomness)
        # TODO 2: apply `problem` when it is set:
        #   feed_stop          -> self.log("ERROR", "<realistic cause>") and return None
        #   duplicates         -> repeat some rows
        #   impossible_values  -> break speed / lat on some rows
        # TODO 3: return the list of dicts
        raise NotImplementedError("Person 1: write the telematics generator")
