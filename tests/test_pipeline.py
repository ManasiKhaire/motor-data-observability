"""End to end, with Gemini switched off (rules only), so tests run anywhere."""
import os

import pytest

from agents.llm import GeminiClient
from agents.orchestrator import Agents, decide
from common.config import database_path, load_settings
from common.io import read_json, set_injection
from common.reference import build_reference
from generators import GENERATORS
from pipeline.db import connect, one, rows
from pipeline.runner import Pipeline

os.environ["LLM_MODE"] = "offline"
SETTINGS = load_settings()
REF = build_reference(SETTINGS)


@pytest.fixture
def world(tmp_path):
    gens = {n: c(SETTINGS, REF, tmp_path) for n, c in GENERATORS.items()}
    conn = connect(database_path(tmp_path))
    pipe = Pipeline(conn, SETTINGS, tmp_path)
    agents = Agents(conn, SETTINGS, tmp_path, GeminiClient(SETTINGS, tmp_path))

    def batch(**problems):
        for name in ["telematics", "policy", "claims", "garage_bills", "customer_kyc"]:
            set_injection(tmp_path, name, problems.get(name))
            gens[name].step()
        return pipe.run_cycle(agents)

    return conn, batch, agents, tmp_path


def incident_for(conn, source):
    return one(conn, "SELECT * FROM incidents WHERE source=? ORDER BY opened_at DESC LIMIT 1", (source,))


def test_clean_data_raises_nothing(world):
    conn, batch, *_ = world
    batch()
    batch()
    assert rows(conn, "SELECT * FROM alerts WHERE status='OPEN'") == []
    assert conn.execute("SELECT COUNT(*) FROM gold_claims_summary").fetchone()[0] > 0


def test_expired_token_story(world):
    conn, batch, agents, out = world
    batch()
    batch(customer_kyc="expired_token")

    leaked = conn.execute("SELECT COUNT(*) FROM silver_customer WHERE masked=0").fetchone()[0]
    assert leaked > 0
    assert one(conn, "SELECT blocked FROM gold_refresh WHERE node='gold_customer_360'")["blocked"] == 1

    inc = incident_for(conn, "customer_kyc")
    assert inc["status"] == "AWAITING_APPROVAL"
    assert inc["category"] == "expired_credential"
    assert inc["fix_action"] == "renew_vault_token"
    assert "403" in inc["root_cause"] or "token" in inc["root_cause"].lower()

    assert decide(conn, inc["incident_id"], True, "tester")
    agents.step()
    inc = incident_for(conn, "customer_kyc")
    assert inc["status"] == "CLOSED"
    assert conn.execute("SELECT COUNT(*) FROM silver_customer WHERE masked=0").fetchone()[0] == 0
    assert one(conn, "SELECT blocked FROM gold_refresh WHERE node='gold_customer_360'")["blocked"] == 0
    assert read_json(out / "control" / "vault_token.json", {})["status"] == "valid"
    assert (out / "reports" / f"{inc['incident_id']}.md").exists()


def test_schema_drift_is_fixed_by_mapping(world):
    conn, batch, agents, _ = world
    batch()
    batch(policy="schema_drift")
    inc = incident_for(conn, "policy")
    assert inc["category"] == "schema_change"
    assert "vehicle_ref" in inc["fix_params"]
    decide(conn, inc["incident_id"], True, "tester")
    agents.step()
    assert incident_for(conn, "policy")["status"] == "CLOSED"
    batch(policy="schema_drift")  # still drifting at the source, but now it loads
    assert one(conn, "SELECT * FROM alerts WHERE source='policy' AND status='OPEN'") is None


def test_bad_values_are_quarantined(world):
    conn, batch, *_ = world
    batch()
    batch(claims="negative_amount")
    assert conn.execute("SELECT COUNT(*) FROM silver_claims WHERE claim_amount < 0").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM quarantine WHERE source='claims'").fetchone()[0] > 0
    assert incident_for(conn, "claims")["category"] == "bad_source_values"


def test_reject_goes_manual_then_closes(world):
    conn, batch, agents, _ = world
    batch()
    batch(garage_bills="total_mismatch")
    inc = incident_for(conn, "garage_bills")
    decide(conn, inc["incident_id"], False, "tester")
    batch()
    assert incident_for(conn, "garage_bills")["status"] == "MANUAL"
    for _ in range(SETTINGS["pipeline"]["resolve_after_clean_batches"] + 1):
        batch()
    assert incident_for(conn, "garage_bills")["status"] == "CLOSED"


class FakeGemini:
    """Stands in for Gemini so the LLM path is tested without a key."""
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def generate_json(self, system, prompt):
        self.prompts.append(prompt)
        return self.answer


def test_gemini_answer_is_used_and_validated(world):
    conn, batch, agents, _ = world
    agents.llm = FakeGemini({"root_cause": "Vault token for masking expired at 12:04.",
                             "category": "expired_credential", "confidence": 0.92,
                             "evidence": ["Vault returned 403 on token refresh"],
                             "recommended_fix": "Renew the token and re-mask."})
    batch()
    batch(customer_kyc="expired_token")
    inc = incident_for(conn, "customer_kyc")
    assert inc["llm_used"] == 1 and inc["root_cause"].startswith("Vault token")
    assert "Vault returned 403" in agents.llm.prompts[0]  # the log line reached the prompt

    agents.llm = FakeGemini({"root_cause": "x", "category": "made_up_category"})
    batch(policy="schema_drift")
    assert incident_for(conn, "policy")["category"] == "schema_change"  # invalid answer -> rules


def test_root_causes_do_not_mix_sources(world):
    conn, batch, *_ = world
    batch()
    batch(customer_kyc="expired_token", policy="schema_drift", claims="negative_amount")
    assert "Vault" not in incident_for(conn, "policy")["root_cause"]
    assert "Vault" not in incident_for(conn, "claims")["root_cause"]
    assert "release v9" in incident_for(conn, "policy")["root_cause"]
