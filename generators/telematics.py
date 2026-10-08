"""Source 1 - Telematics (IoT): driving readings from devices fitted in insured cars."""
from datetime import timedelta

from common.io import utc_now
from generators.base import BaseGenerator


class TelematicsGenerator(BaseGenerator):
    name = "telematics"
    problems = {
        "feed_stop": "Device broker goes down: no files arrive at all",
        "duplicates": "Same readings sent twice (about 30% of rows repeated)",
        "impossible_values": "Speeds of 400 km/h and GPS points outside the map",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.odometer = {d["device_id"]: d["odometer"] for d in self.ref.devices}

    def make_batch(self, n, problem):
        if problem == "feed_stop":
            self.log("ERROR", "MQTT broker connection refused: broker-03 unreachable",
                     component="mqtt-broker-03")
            return None

        rng, now, rows = self.rng, utc_now(), []
        for d in rng.sample(self.ref.devices, min(n, len(self.ref.devices))):
            speed = max(0.0, rng.gauss(45, 20))
            self.odometer[d["device_id"]] += round(speed * 5 / 3600, 3)
            rows.append({
                "device_id": d["device_id"], "vehicle_id": d["vehicle_id"], "policy_id": d["policy_id"],
                "timestamp": (now - timedelta(milliseconds=rng.randint(0, 4999))).isoformat(),
                "speed_kmph": round(speed, 1),
                "harsh_brake": rng.random() < 0.05,
                "harsh_accel": rng.random() < 0.04,
                "lat": round(d["lat"] + rng.uniform(-0.08, 0.08), 5),
                "lon": round(d["lon"] + rng.uniform(-0.08, 0.08), 5),
                "odometer_km": round(self.odometer[d["device_id"]], 1),
            })

        if problem == "duplicates":
            rows += [dict(r) for r in rng.sample(rows, max(1, len(rows) * 3 // 10))]
        elif problem == "impossible_values":
            for r in rng.sample(rows, max(1, len(rows) // 5)):
                r["speed_kmph"] = 400.0
                r["lat"] = 999.0
            self.log("WARN", "Firmware 2.4.1 rollout on devices: sensor calibration reset")
        return rows
