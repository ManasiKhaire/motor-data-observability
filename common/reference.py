"""Shared master data: customers, vehicles, policies, devices and garages.

Every generator calls build_reference() with the same seed, so all five
sources agree on the same IDs (customer_id, policy_id, vehicle_id ...)
without needing a shared database. This is what makes joins and lineage work.
"""
import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta

from faker import Faker

CITIES = [
    ("Pune", 18.52, 73.86), ("Mumbai", 19.08, 72.88), ("Bengaluru", 12.97, 77.59),
    ("Delhi", 28.61, 77.21), ("Hyderabad", 17.39, 78.49), ("Chennai", 13.08, 80.27),
]
STATE_CODES = {"Pune": "MH12", "Mumbai": "MH01", "Bengaluru": "KA01",
               "Delhi": "DL01", "Hyderabad": "TS09", "Chennai": "TN01"}
MAKES = {
    "Maruti Suzuki": ["Swift", "Baleno", "Brezza"],
    "Hyundai": ["i20", "Creta", "Venue"],
    "Tata": ["Nexon", "Punch", "Altroz"],
    "Mahindra": ["XUV700", "Thar", "Scorpio-N"],
    "Honda": ["City", "Amaze"],
}
COVER_TYPES = ["Comprehensive", "Third Party", "Own Damage"]


@dataclass
class Reference:
    customers: list = field(default_factory=list)
    vehicles: list = field(default_factory=list)
    policies: list = field(default_factory=list)
    devices: list = field(default_factory=list)
    garages: list = field(default_factory=list)

    @property
    def active_policies(self):
        today = date.today()
        return [p for p in self.policies if p["end_date"] >= today]

    @property
    def expired_policies(self):
        today = date.today()
        return [p for p in self.policies if p["end_date"] < today]


def _pan(rng: random.Random) -> str:
    letters = string.ascii_uppercase
    return ("".join(rng.choice(letters) for _ in range(3)) + "P"
            + rng.choice(letters) + f"{rng.randint(0, 9999):04d}" + rng.choice(letters))


def build_reference(settings: dict) -> Reference:
    seed = settings["seed"]
    cfg = settings["reference"]
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    ref = Reference()
    today = date.today()

    for i in range(1, cfg["customers"] + 1):
        city, lat, lon = rng.choice(CITIES)
        customer_id = f"CUST{i:06d}"
        ref.customers.append({
            "customer_id": customer_id,
            "name": fake.name(),
            "phone": f"+91{rng.randint(7000000000, 9999999999)}",
            "email": fake.email(),
            "licence_no": f"{STATE_CODES[city]}{rng.randint(2005, 2023)}{rng.randint(0, 9999999):07d}",
            "pan": _pan(rng),
            "address": fake.address().replace("\n", ", "),
            "dob": fake.date_of_birth(minimum_age=19, maximum_age=70).isoformat(),
            "city": city,
            "lat": lat,
            "lon": lon,
        })

        make = rng.choice(list(MAKES))
        vehicle_id = f"VEH{i:06d}"
        reg_no = (f"{STATE_CODES[city]}{rng.choice(string.ascii_uppercase)}"
                  f"{rng.choice(string.ascii_uppercase)}{rng.randint(1000, 9999)}")
        ref.vehicles.append({
            "vehicle_id": vehicle_id, "customer_id": customer_id, "reg_no": reg_no,
            "make": make, "model": rng.choice(MAKES[make]), "year": rng.randint(2014, 2025),
        })

        expired = rng.random() < cfg["expired_share"]
        start = today - timedelta(days=rng.randint(400, 700) if expired else rng.randint(1, 300))
        sum_insured = rng.randint(3, 25) * 50_000
        ref.policies.append({
            "policy_id": f"POL{i:06d}", "customer_id": customer_id, "vehicle_id": vehicle_id,
            "reg_no": reg_no, "make": make, "model": ref.vehicles[-1]["model"],
            "year": ref.vehicles[-1]["year"], "cover_type": rng.choice(COVER_TYPES),
            "sum_insured": sum_insured, "premium": round(sum_insured * rng.uniform(0.02, 0.045), 2),
            "start_date": start, "end_date": start + timedelta(days=365),
        })

        if rng.random() < cfg["device_share"]:
            ref.devices.append({
                "device_id": f"DEV{i:06d}", "vehicle_id": vehicle_id, "policy_id": f"POL{i:06d}",
                "lat": lat, "lon": lon, "odometer": rng.randint(5_000, 120_000),
            })

    for g in range(1, cfg["garages"] + 1):
        city = rng.choice(CITIES)[0]
        ref.garages.append({"garage_id": f"GAR{g:04d}", "city": city,
                            "name": f"{fake.last_name()} Motors {city}"})
    return ref
