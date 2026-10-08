"""Dashboard:  streamlit run app/dashboard.py

Five pages, refreshing every few seconds:
  Pipeline map  |  Data quality  |  Compliance  |  Lineage & SLA  |  Incidents (approve / reject)
The sidebar can switch problems on and off for live demos.
"""
import getpass
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from agents.llm import GeminiClient  # noqa: E402
from agents.orchestrator import decide  # noqa: E402
from common.config import database_path, load_settings, output_dir  # noqa: E402
from common.io import read_json, set_injection  # noqa: E402
from generators import GENERATORS  # noqa: E402
from pipeline import lineage  # noqa: E402
from pipeline.db import connect, rows  # noqa: E402

st.set_page_config(page_title="Motor Data Observability", layout="wide")

SETTINGS = load_settings()
OUT = output_dir(SETTINGS, sys.argv[1] if len(sys.argv) > 1 else None)
FILL = {"green": "#D7F2E3", "yellow": "#FCEFC7", "red": "#F9D6D5"}
LINE = {"green": "#2E9E5B", "yellow": "#C98A04", "red": "#C8372D"}
STATUS_ICON = {"INVESTIGATING": "🔎", "AWAITING_APPROVAL": "⏳", "APPROVED": "✅", "CLOSED": "✔️",
               "MANUAL": "🛠️", "FIX_FAILED": "⚠️", "REJECTED": "✋"}


@st.cache_resource
def get_conn():
    return connect(database_path(OUT))


conn = get_conn()


def q(sql, params=()):
    return rows(conn, sql, params)


def minutes_since(ts):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60


# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Motor Data Observability")
    st.caption(f"Data folder: `{OUT}`")
    st.caption(f"LLM: {GeminiClient(SETTINGS, OUT).status}")

    st.subheader("Inject a problem")
    src = st.selectbox("Source", list(GENERATORS), key="inj_src")
    problem = st.selectbox("Problem", ["off"] + list(GENERATORS[src].problems), key="inj_problem")
    if problem != "off":
        st.caption(GENERATORS[src].problems[problem])
    if st.button("Apply", use_container_width=True):
        set_injection(OUT, src, problem)
        st.toast(f"{src}: {problem}")

    @st.fragment(run_every="3s")
    def switch_status():
        current = read_json(OUT / "control" / "inject.json", {})
        token = read_json(OUT / "control" / "vault_token.json", {"status": "valid"}).get("status")
        on = ", ".join(f"{k}={v}" for k, v in current.items() if v not in (None, "off")) or "nothing"
        st.caption(f"Problems on now: {on}")
        st.caption(f"Vault token: {token}")

    switch_status()


@st.fragment(run_every="3s")
def body():
    if not q("SELECT 1 FROM pipeline_runs LIMIT 1"):
        st.info("No pipeline runs yet. Start the generators (`python run_all.py`) and the pipeline "
                "(`python run_pipeline.py`) in two other terminals.")
        return

    open_alerts = q("SELECT * FROM alerts WHERE status='OPEN' ORDER BY first_seen DESC")
    incidents = q("SELECT * FROM incidents ORDER BY opened_at DESC")
    waiting = [i for i in incidents if i["status"] == "AWAITING_APPROVAL"]
    closed = [i for i in incidents if i["status"] == "CLOSED" and i["closed_at"]]
    mttr = (sum((datetime.fromisoformat(i["closed_at"]) - datetime.fromisoformat(i["opened_at"])).total_seconds()
                for i in closed) / 60 / len(closed)) if closed else None
    total_rows = conn.execute("SELECT COALESCE(SUM(rows), 0) FROM file_registry").fetchone()[0]

    c = st.columns(5)
    c[0].metric("Open alerts", len(open_alerts))
    c[1].metric("Active incidents", len([i for i in incidents if i["status"] != "CLOSED"]))
    c[2].metric("Waiting for approval", len(waiting))
    c[3].metric("Avg time to resolve", f"{mttr:.1f} min" if mttr is not None else "-")
    c[4].metric("Rows processed", f"{total_rows:,}")

    tabs = st.tabs(["Pipeline map", "Data quality", "Compliance", "Lineage & SLA", "Incidents"])
    with tabs[0]:
        pipeline_map()
    with tabs[1]:
        data_quality(open_alerts)
    with tabs[2]:
        compliance()
    with tabs[3]:
        lineage_sla()
    with tabs[4]:
        incident_center(incidents)


# ---------------- pages ----------------
def pipeline_map():
    status = {r["node"]: r for r in q("SELECT * FROM node_status")}
    layers = {}
    for node, meta in lineage.nodes().items():
        layers.setdefault(meta["layer"], []).append(node)
    order = ["source", "bronze", "step", "silver", "gold"]
    dot = ['digraph G { rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.7;',
           'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, penwidth=1.6];',
           'edge [color="#8A94A3", arrowsize=0.6];']
    for layer in order:
        dot.append("{ rank=same; " + " ".join(f'"{n}";' for n in layers.get(layer, [])) + " }")
    for node in lineage.nodes():
        s = status.get(node, {}).get("status", "green")
        label = node.replace("src_", "").replace("_", " ")
        dot.append(f'"{node}" [label="{label}", fillcolor="{FILL[s]}", color="{LINE[s]}"];')
    for a, b in lineage.edges():
        dot.append(f'"{a}" -> "{b}";')
    dot.append("}")
    st.graphviz_chart("\n".join(dot), use_container_width=True)
    st.caption("Green: healthy · Yellow: at risk or minor issue · Red: failing or blocked. "
               "Columns: sources → bronze → masking → silver → gold.")
    problems = [r for r in status.values() if r["status"] != "green" and r["detail"]]
    if problems:
        st.dataframe([{"node": p["node"], "status": p["status"], "why": p["detail"]} for p in problems],
                     use_container_width=True, hide_index=True)


def data_quality(open_alerts):
    st.subheader("Open alerts")
    if open_alerts:
        st.dataframe([{"severity": a["severity"], "source": a["source"], "check": a["check_name"],
                       "problem": a["problem"], "seen": a["occurrences"], "since": a["first_seen"][11:19],
                       "incident": a["incident_id"]} for a in open_alerts],
                     use_container_width=True, hide_index=True)
    else:
        st.success("No open alerts. Every check passed on the latest batches.")

    left, right = st.columns(2)
    with left:
        st.subheader("Rows stopped by checks")
        quarantine = q("SELECT source || ' · ' || check_name AS reason, COUNT(*) AS rows FROM quarantine "
                       "WHERE released=0 GROUP BY 1 ORDER BY 2 DESC")
        if quarantine:
            st.bar_chart(quarantine, x="reason", y="rows", horizontal=True)
        else:
            st.caption("Nothing in quarantine.")
    with right:
        st.subheader("Rows loaded per source")
        loaded = q("SELECT source, SUM(rows) AS rows, COUNT(*) AS files, MAX(ingested_at) AS last_file "
                   "FROM file_registry GROUP BY source")
        st.dataframe([dict(r, last_file=r["last_file"][11:19]) for r in loaded],
                     use_container_width=True, hide_index=True)

    st.subheader("Alert history")
    st.dataframe(q("SELECT status, severity, source, check_name AS check_, problem, first_seen, last_seen "
                   "FROM alerts ORDER BY last_seen DESC LIMIT 50"), use_container_width=True, hide_index=True)


def compliance():
    total, leaked = conn.execute("SELECT COUNT(*), COALESCE(SUM(masked=0), 0) FROM silver_customer").fetchone()
    pii_open = q("SELECT * FROM alerts WHERE check_name='pii' AND status='OPEN'")
    blocked = q("SELECT * FROM gold_refresh WHERE blocked=1")
    risk = "AT RISK" if (leaked or pii_open) else "PASS"

    cols = st.columns(3)
    for col, (name, text) in zip(cols, [("DPDP Act 2023", "Personal data protected"),
                                        ("IRDAI data guidelines", "Policyholder data secured"),
                                        ("Internal PII policy", "PII masked before Silver")]):
        with col:
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.markdown(f"### {'🟢 PASS' if risk == 'PASS' else '🔴 AT RISK'}")
                st.caption(text)

    st.metric("Unmasked customer rows in Silver", f"{leaked} of {total}")
    if blocked:
        st.error("Publishing halted: " + ", ".join(f"{b['node']} ({b['reason']})" for b in blocked))
    else:
        st.success("All Gold data products are publishing.")

    st.subheader("Masked customer data (sample)")
    st.dataframe(q("SELECT customer_id, name, phone, pan, licence_no, city, masked FROM silver_customer "
                   "ORDER BY updated_at DESC LIMIT 10"), use_container_width=True, hide_index=True)
    st.subheader("Compliance report (last published copy)")
    try:
        st.dataframe(q("SELECT * FROM gold_compliance_report"), use_container_width=True, hide_index=True)
    except Exception:
        st.caption("Not built yet.")


def lineage_sla():
    st.subheader("Data products and their SLAs")
    out = []
    for g in q("SELECT * FROM gold_refresh"):
        meta = lineage.nodes().get(g["node"], {})
        since = minutes_since(g["last_refreshed"])
        sla = meta.get("sla_minutes", 60)
        sources = [n.replace("src_", "") for n in lineage.upstream(g["node"]) if n.startswith("src_")]
        out.append({"data product": g["node"].replace("gold_", ""), "owner": meta.get("owner"),
                    "SLA (min)": sla, "minutes since refresh": round(since, 1),
                    "minutes left": round(sla - since, 1),
                    "status": "BLOCKED" if g["blocked"] else ("BREACHED" if since > sla else "OK"),
                    "built from": ", ".join(sorted(sources))})
    st.dataframe(out, use_container_width=True, hide_index=True)

    st.subheader("What breaks if a step fails?")
    node = st.selectbox("Pick a step", list(lineage.nodes()), key="lin_node")
    down = lineage.downstream(node)
    up = lineage.upstream(node)
    c1, c2 = st.columns(2)
    c1.markdown("**Feeds into (downstream)**\n\n" + ("\n".join(f"- {d}" for d in down) or "_nothing_"))
    c2.markdown("**Depends on (upstream)**\n\n" + ("\n".join(f"- {u}" for u in up) or "_nothing_"))


def incident_center(incidents):
    if not incidents:
        st.success("No incidents so far.")
        return
    labels = {f"{STATUS_ICON.get(i['status'], '')} {i['incident_id']} · {i['status']} · {i['title'][:70]}": i
              for i in incidents}
    choice = st.selectbox("Incident", list(labels), key="inc_pick")
    inc = labels[choice]

    st.markdown(f"### {inc['incident_id']}: {inc['title']}")
    st.caption(f"Severity {inc['severity']} · opened {inc['opened_at'][11:19]} UTC · status {inc['status']}"
               + (" · analysed by Gemini" if inc["llm_used"] else " · analysed by rules"))

    if inc["status"] == "INVESTIGATING":
        st.info("Agents are investigating...")
    if inc["root_cause"]:
        left, right = st.columns([3, 2])
        with left:
            st.markdown("**Root cause**")
            st.write(f"{inc['root_cause']}  \n*{inc['category']}, confidence {(inc['confidence'] or 0):.0%}*")
            evidence = json.loads(inc["evidence"] or "[]")
            if evidence:
                st.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in evidence))
            st.markdown("**Proposed fix**")
            st.write(inc["proposed_fix"])
        with right:
            impact = json.loads(inc["impact"] or "{}")
            st.markdown("**Impact**")
            st.write(impact.get("summary", ""))
            if impact.get("gold"):
                live = {r["node"]: r for r in q("SELECT * FROM gold_refresh")}
                table = []
                for g in impact["gold"]:
                    r = live.get(g["node"], {})
                    left = g["sla_minutes"] - minutes_since(r["last_refreshed"]) if r else g["minutes_left"]
                    table.append({"product": g["node"].replace("gold_", ""), "owner": g["owner"],
                                  "blocked now": bool(r.get("blocked")), "SLA left (min)": round(max(0, left), 1)})
                st.dataframe(table, use_container_width=True, hide_index=True)

    if inc["status"] == "AWAITING_APPROVAL":
        st.warning("This fix will only run after you approve it. It is tested on a small sample first.")
        a, r, _ = st.columns([1, 1, 4])
        if a.button("Approve fix", type="primary", key=f"ok_{inc['incident_id']}"):
            decide(conn, inc["incident_id"], True, getpass.getuser())
            st.toast("Approved. The fix runs on the next pipeline cycle.")
        if r.button("Reject", key=f"no_{inc['incident_id']}"):
            decide(conn, inc["incident_id"], False, getpass.getuser())
            st.toast("Rejected. Marked for manual fixing.")

    st.markdown("**Timeline**")
    for e in q("SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (inc["incident_id"],)):
        st.markdown(f"`{e['ts'][11:19]}` **{e['actor']}**: {e['message']}")

    if inc["report"]:
        with st.expander("Final report", expanded=inc["status"] == "CLOSED"):
            st.markdown(inc["report"])


body()
