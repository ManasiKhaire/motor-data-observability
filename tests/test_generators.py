"""Checks each source against its contract. A source whose generator is not written yet is skipped.

Run:  python -m pytest -q
"""
import csv
import json
import re

import pytest

from common.config import ROOT, load_settings
from common.io import read_json, set_injection
from common.reference import build_reference
from generators import GENERATORS

SETTINGS = load_settings()
REF = build_reference(SETTINGS)
CONTRACTS = json.loads((ROOT / "schemas" / "source_contracts.json").read_text())


def make(name, tmp_path):
    return GENERATORS[name](SETTINGS, REF, tmp_path)


def step(name, tmp_path, problem=None):
    try:
        if name == "garage_bills":
            make("claims", tmp_path).step()
        return make(name, tmp_path).step(problem)
    except NotImplementedError:
        pytest.skip(f"{name} generator not written yet")


def read_rows(path):
    if path.suffix == ".json":
        return [json.loads(line) for line in path.read_text().splitlines()]
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_reference_is_stable():
    again = build_reference(SETTINGS)
    assert [p["policy_id"] for p in again.policies] == [p["policy_id"] for p in REF.policies]


@pytest.mark.parametrize("name", list(GENERATORS))
def test_normal_batch_matches_contract(name, tmp_path):
    path = step(name, tmp_path)
    assert path is not None, "a normal batch should write a file"
    rows = read_rows(path)
    assert rows, "a normal batch should have rows"
    expected = {k for k in CONTRACTS[name] if not k.startswith("_")}
    assert set(rows[0]) == expected, f"columns differ from schemas/source_contracts.json"
    for col, rule in CONTRACTS[name].items():
        if col.startswith("_"):
            continue
        if not rule.get("nullable", True):
            assert all(r[col] not in (None, "") for r in rows), f"{col} is empty"
        if "pattern" in rule:
            assert all(re.match(rule["pattern"], str(r[col])) for r in rows), f"{col} format"


@pytest.mark.parametrize("name", list(GENERATORS))
def test_ids_match_shared_reference(name, tmp_path):
    path = step(name, tmp_path)
    rows = read_rows(path)
    known = {
        "policy_id": {p["policy_id"] for p in REF.policies},
        "customer_id": {c["customer_id"] for c in REF.customers},
        "vehicle_id": {v["vehicle_id"] for v in REF.vehicles},
    }
    for col, ids in known.items():
        if col in rows[0]:
            assert all(r[col] in ids for r in rows), f"{col} not from self.ref"


@pytest.mark.parametrize("name,problem", [(n, p) for n, c in GENERATORS.items() for p in c.problems])
def test_every_problem_runs(name, problem, tmp_path):
    set_injection(tmp_path, name, problem)
    step(name, tmp_path)  # must not crash; some problems write nothing on purpose


def test_expired_token_flips_vault_status(tmp_path):
    step("customer_kyc", tmp_path, "expired_token")
    assert read_json(tmp_path / "control" / "vault_token.json", {}).get("status") == "expired"
