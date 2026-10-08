"""Observability console:  streamlit run app/dashboard.py

Tabs: Overview · Service map · Data quality · Compliance · SLA & lineage · Incidents
Refreshes every few seconds. The sidebar switches problems on and off for live demos.
"""
import getpass
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agents.llm import GeminiClient  # noqa: E402
from agents.orchestrator import decide  # noqa: E402
from app import charts, data  # noqa: E402
from common.config import database_path, load_settings, output_dir  # noqa: E402
from common.io import read_json, set_injection  # noqa: E402
from generators import GENERATORS  # noqa: E402
from pipeline import lineage  # noqa: E402
from pipeline.db import connect, rows  # noqa: E402

st.set_page_config(page_title="Motor Data Observability", layout="wide", page_icon="◉")

SETTINGS = load_settings()
OUT = output_dir(SETTINGS, sys.argv[1] if len(sys.argv) > 1 else None)

st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1500px;}
  [data-testid="stHeader"] {background: transparent; height: 2.2rem;}
  [data-testid="stToolbar"] {top: 0.2rem;}
  h2 {padding-top: 0 !important;}
  [data-testid="stVerticalBlockBorderWrapper"] {background: #161b22; border-color: #30363d !important; border-radius: 10px;}
  .kpi-label {font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #8b949e; margin-bottom: 2px;}
  .kpi-value {font-size: 30px; font-weight: 600; color: #e6edf3; line-height: 1.15;}
  .kpi-sub {font-size: 12px; color: #8b949e;}
  .section {font-size: 13px; font-weight: 600; color: #e6edf3; margin: 2px 0 6px;}
  .muted {color: #8b949e; font-size: 12px;}
  .pill {display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
         border: 1px solid; margin-right: 6px;}
  .p-critical {color: #ff7b72; border-color: #d03b3b; background: rgba(208,59,59,.12);}
  .p-high {color: #ec835a; border-color: #ec835a; background: rgba(236,131,90,.12);}
  .p-medium {color: #fab219; border-color: #fab219; background: rgba(250,178,25,.10);}
  .p-good {color: #3fb950; border-color: #0ca30c; background: rgba(12,163,12,.12);}
  .p-info {color: #79c0ff; border-color: #3987e5; background: rgba(57,135,229,.12);}
  .live {display:inline-block; width:8px; height:8px; border-radius:50%; background:#0ca30c; margin-right:6px;
         box-shadow: 0 0 0 3px rgba(12,163,12,.25);}
  .ai-box {border-left: 3px solid #3987e5; padding: 6px 12px; background: rgba(57,135,229,.06); border-radius: 4px;}
  .event {font-size: 13px; padding: 3px 0; border-bottom: 1px solid #21262d;}
  .event code {color: #8b949e; background: none;}
</style>""", unsafe_allow_html=True)

SEV_PILL = {"CRITICAL": "p-critical", "HIGH": "p-high", "MEDIUM": "p-medium", "LOW": "p-info"}
STATE_PILL = {"CLOSED": ("Resolved", "p-good"), "AWAITING_APPROVAL": ("Awaiting approval", "p-medium"),
              "INVESTIGATING": ("Investigating", "p-info"), "APPROVED": ("Fixing", "p-info"),
              "MANUAL": ("Manual fix", "p-high"), "FIX_FAILED": ("Fix failed", "p-critical")}


@st.cache_resource
def get_conn():
    return connect(database_path(OUT))


conn = get_conn()


def q(sql, params=()):
    return rows(conn, sql, params)


def chart(c):
    st.altair_chart(c, use_container_width=True, theme=None)


def pill(text, cls):
    return f'<span class="pill {cls}">{text}</span>'


def kpi(label, value, sub="", spark=None, colour="#3987e5"):
    with st.container(border=True):
        st.markdown(f'<div class="kpi-label">{label}</div><div class="kpi-value">{value}</div>'
                    f'<div class="kpi-sub">{sub}</div>', unsafe_allow_html=True)
        if spark is not None and len(spark) > 1:
            chart(charts.sparkline(list(spark), colour))


def empty(msg):
    st.markdown(f'<div class="muted" style="padding:40px 0;text-align:center">{msg}</div>', unsafe_allow_html=True)


# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown("### ◉ Motor Data Observability")
    st.markdown(f'<div class="muted">Data folder<br><code>{OUT}</code></div>', unsafe_allow_html=True)
    st.divider()
    st.markdown('<div class="section">Chaos controls</div>', unsafe_allow_html=True)
    src = st.selectbox("Source", list(GENERATORS), key="inj_src")
    problem = st.selectbox("Problem", ["off"] + list(GENERATORS[src].problems), key="inj_problem")
    if problem != "off":
        st.caption(GENERATORS[src].problems[problem])
    if st.button("Inject", use_container_width=True, type="primary"):
        set_injection(OUT, src, problem)
        st.toast(f"{src}: {problem}")
    if st.button("Switch all off", use_container_width=True):
        for name in GENERATORS:
            set_injection(OUT, name, "off")
        st.toast("All problems switched off")

    @st.fragment(run_every="3s")
    def switch_status():
        current = read_json(OUT / "control" / "inject.json", {})
        token = read_json(OUT / "control" / "vault_token.json", {"status": "valid"}).get("status")
        on = [f"{k}: {v}" for k, v in current.items() if v not in (None, "off")]
        st.markdown('<div class="section" style="margin-top:12px">Active problems</div>' +
                    ("".join(f'<div>{pill(o, "p-high")}</div>' for o in on) or '<div class="muted">None</div>'),
                    unsafe_allow_html=True)
        st.markdown(f'<div class="muted" style="margin-top:8px">Vault token: '
                    f'{pill(token, "p-critical" if token == "expired" else "p-good")}</div>', unsafe_allow_html=True)

    switch_status()
    st.divider()
    st.markdown(f'<div class="muted">AI: {GeminiClient(SETTINGS, OUT).status}</div>', unsafe_allow_html=True)


# ---------------- pages ----------------
def local(ts):
    """Show times in the laptop's own time zone (charts do the same in the browser)."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.astimezone().strftime("%H:%M:%S")


def header(k):
    status = "All systems healthy" if k["open_alerts"] == 0 else f"{k['open_alerts']} open alerts · {k['critical']} critical"
    st.markdown(f'<h2 style="margin-bottom:0">Motor insurance data platform</h2>'
                f'<div class="muted" style="margin-bottom:6px"><span class="live"></span>Live · updated {local(k["now"])}'
                f' · {status}</div>', unsafe_allow_html=True)


def health_gauge(score):
    colour = charts.STATUS["good"] if score >= 80 else charts.STATUS["warning"] if score >= 50 else charts.STATUS["critical"]
    d = pd.DataFrame({"part": ["score", "rest"], "v": [score, 100 - score]})
    arc = alt.Chart(d).mark_arc(innerRadius=50, outerRadius=64, cornerRadius=4).encode(
        theta=alt.Theta("v:Q", stack=True, sort=None),
        color=alt.Color("part:N", scale=alt.Scale(domain=["score", "rest"], range=[colour, "#21262d"]), legend=None),
        order=alt.Order("part:N", sort="descending"))
    txt = alt.Chart(pd.DataFrame({"t": [f"{score}"]})).mark_text(fontSize=30, fontWeight=600, color="#e6edf3", dy=-4).encode(text="t:N")
    sub = alt.Chart(pd.DataFrame({"t": ["/ 100"]})).mark_text(fontSize=11, color="#8b949e", dy=18).encode(text="t:N")
    return (arc + txt + sub).properties(height=150)


def overview(k):
    tp = data.throughput(conn, 15, 20)
    qt = data.quality_trend(conn, 15, 30)

    cols = st.columns([1.2, 1, 1, 1, 1])
    with cols[0]:
        with st.container(border=True):
            st.markdown('<div class="kpi-label">Platform health</div>', unsafe_allow_html=True)
            chart(health_gauge(int(k["health"])))
            label = "Healthy" if k["health"] >= 80 else "Degraded" if k["health"] >= 50 else "Critical"
            st.markdown(f'<div class="kpi-sub" style="text-align:center">{label} · {k["blocked"]} data products blocked</div>',
                        unsafe_allow_html=True)
    with cols[1]:
        spark = tp.groupby("time")["rows_per_min"].sum().tail(20) if not tp.empty else None
        kpi("Throughput", f"{k['rows_per_min']:,.0f}", "rows / min · last 5 min", spark)
    with cols[2]:
        kpi("Data quality", f"{k['pass_rate']:.1f}%" if k["pass_rate"] is not None else "–",
            "rows passing all checks", qt["pass_rate"].tail(20) if not qt.empty else None, "#199e70")
    with cols[3]:
        kpi("Open alerts", k["open_alerts"], f"{k['critical']} critical · {k['active_incidents']} incidents")
        kpi("Awaiting approval", k["waiting"], "fixes proposed by agents")
    with cols[4]:
        kpi("MTTR", f"{k['mttr']:.1f} min" if k["mttr"] is not None else "–", "mean time to resolve")
        kpi("Rows processed", f"{k['total_rows']:,}", f"{k['ai_incidents']} incidents analysed by Gemini")

    c1, c2 = st.columns([2, 1])
    with c1:
        with st.container(border=True):
            st.markdown('<div class="section">Throughput by source</div>', unsafe_allow_html=True)
            if not tp.empty:
                chart(charts.throughput(tp))
            else:
                empty("Waiting for data")
    with c2:
        with st.container(border=True):
            st.markdown('<div class="section">Data freshness</div>', unsafe_allow_html=True)
            fr = data.freshness(conn, SETTINGS)
            if not fr.empty:
                chart(charts.freshness(fr))
            else:
                empty("Waiting for data")
            st.markdown('<div class="muted">Seconds since last file; dashed line = alert threshold</div>',
                        unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown('<div class="section">Data quality score <span class="muted">· dashed line = 95% target</span></div>',
                        unsafe_allow_html=True)
            if not qt.empty:
                chart(charts.quality(qt))
            else:
                empty("Waiting for data")
    with c2:
        with st.container(border=True):
            st.markdown('<div class="section">New alerts by severity</div>', unsafe_allow_html=True)
            ae = data.alert_events(conn, 30, 60)
            if not ae.empty:
                chart(charts.alerts_by_severity(ae))
            else:
                empty("No alerts in the last 30 minutes")

    c1, c2 = st.columns([2, 1])
    with c1:
        with st.container(border=True):
            st.markdown('<div class="section">Failures by source and check · last 15 min</div>', unsafe_allow_html=True)
            chart(charts.failure_heatmap(data.failure_matrix(conn, 15)))
    with c2:
        with st.container(border=True):
            st.markdown('<div class="section">Active incidents</div>', unsafe_allow_html=True)
            active = q("SELECT * FROM incidents WHERE status!='CLOSED' ORDER BY opened_at DESC LIMIT 5")
            if not active:
                empty("No active incidents")
            for i in active:
                label, cls = STATE_PILL.get(i["status"], (i["status"], "p-info"))
                st.markdown(f'<div class="event">{pill(i["severity"], SEV_PILL.get(i["severity"], "p-info"))}'
                            f'{pill(label, cls)}<b>{i["incident_id"]}</b><br>'
                            f'<span class="muted">{(i["root_cause"] or i["title"])[:140]}</span></div>',
                            unsafe_allow_html=True)


def service_map():
    status = {r["node"]: r for r in q("SELECT * FROM node_status")}
    metrics = data.node_metrics(conn, 1)
    fill = {"green": "#0f2a1a", "yellow": "#2e2510", "red": "#3a1416"}
    line = {"green": "#0ca30c", "yellow": "#fab219", "red": "#d03b3b"}
    layers = {}
    for node, meta in lineage.nodes().items():
        layers.setdefault(meta["layer"], []).append(node)
    dot = ['digraph G { rankdir=LR; bgcolor="transparent"; nodesep=0.3; ranksep=0.9; pad=0.2;',
           'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, fontcolor="#e6edf3", penwidth=1.6, margin="0.18,0.08"];',
           'edge [color="#484f58", arrowsize=0.6, penwidth=1.1];']
    for layer in ["source", "bronze", "step", "silver", "gold"]:
        dot.append("{ rank=same; " + " ".join(f'"{n}";' for n in layers.get(layer, [])) + " }")
    for node in lineage.nodes():
        s = status.get(node, {}).get("status", "green")
        name = node.replace("src_", "").replace("_", " ")
        sub = metrics.get(node, "")
        label = f'<<b>{name}</b><br/><font point-size="9" color="#8b949e">{sub}</font>>'
        dot.append(f'"{node}" [label={label}, fillcolor="{fill[s]}", color="{line[s]}"];')
    for a, b in lineage.edges():
        s = status.get(a, {}).get("status", "green")
        colour = line[s] if s != "green" else "#484f58"
        dot.append(f'"{a}" -> "{b}" [color="{colour}"];')
    dot.append("}")
    with st.container(border=True):
        st.markdown('<div class="section">Service map · sources → bronze → masking → silver → gold</div>',
                    unsafe_allow_html=True)
        st.graphviz_chart("\n".join(dot), use_container_width=True)
        st.markdown(f'{pill("healthy", "p-good")}{pill("at risk", "p-medium")}{pill("failing / blocked", "p-critical")}'
                    '<span class="muted">Edges leaving a failing node are coloured too.</span>', unsafe_allow_html=True)
    problems = [r for r in status.values() if r["status"] != "green" and r["detail"]]
    if problems:
        with st.container(border=True):
            st.markdown('<div class="section">Why nodes are not green</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame([{"node": p["node"], "state": p["status"], "reason": p["detail"]} for p in problems]),
                         use_container_width=True, hide_index=True)


def quality_page():
    c1, c2 = st.columns([1, 1])
    with c1:
        with st.container(border=True):
            st.markdown('<div class="section">Rows stopped by checks (quarantine)</div>', unsafe_allow_html=True)
            qd = data.df(conn, "SELECT source, check_name, source || ' · ' || check_name AS reason, COUNT(*) AS rows "
                               "FROM quarantine WHERE released=0 GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 12")
            if not qd.empty:
                chart(charts.quarantine_reasons(qd))
            else:
                empty("Nothing in quarantine")
    with c2:
        with st.container(border=True):
            st.markdown('<div class="section">Pass rate by source · last 15 min</div>', unsafe_allow_html=True)
            b = data.batches(conn, 15)
            if b.empty:
                empty("Waiting for data")
            else:
                s = b.groupby("source", as_index=False)[["rows", "good"]].sum()
                s["pass_rate"] = (100 * s["good"] / s["rows"]).round(1)
                bars = alt.Chart(s).mark_bar(cornerRadiusEnd=4, height=16).encode(
                    x=alt.X("pass_rate:Q", title="% rows passing", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("source:N", sort=charts.SOURCES, title=None),
                    color=alt.Color("source:N", scale=charts.SOURCE_SCALE, legend=None),
                    tooltip=["source:N", "rows:Q", "good:Q", "pass_rate:Q"])
                text = alt.Chart(s).mark_text(align="left", dx=6, color=charts.INK_2, fontSize=11).encode(
                    x="pass_rate:Q", y=alt.Y("source:N", sort=charts.SOURCES), text=alt.Text("pass_rate:Q", format=".1f"))
                chart((bars + text).properties(height=190))
    with st.container(border=True):
        st.markdown('<div class="section">Alerts</div>', unsafe_allow_html=True)
        alerts = data.df(conn, "SELECT status, severity, source, check_name AS check_, problem, occurrences AS seen, "
                               "first_seen AS first, last_seen AS last, incident_id "
                               "FROM alerts ORDER BY status='OPEN' DESC, last_seen DESC LIMIT 60")
        if not alerts.empty:
            alerts["first"] = alerts["first"].map(local)
            alerts["last"] = alerts["last"].map(local)
        if not alerts.empty:
            st.dataframe(alerts, use_container_width=True, hide_index=True)
        else:
            empty("No alerts yet")


def compliance_page():
    total, leaked = conn.execute("SELECT COUNT(*), COALESCE(SUM(masked=0), 0) FROM silver_customer").fetchone()
    pii_open = q("SELECT * FROM alerts WHERE check_name='pii' AND status='OPEN'")
    blocked = q("SELECT * FROM gold_refresh WHERE blocked=1")
    at_risk = bool(leaked or pii_open)
    cols = st.columns(4)
    for col, (name, text) in zip(cols[:3], [("DPDP Act 2023", "Personal data protected"),
                                            ("IRDAI data guidelines", "Policyholder data secured"),
                                            ("Internal PII policy", "PII masked before Silver")]):
        with col:
            with st.container(border=True):
                st.markdown(f'<div class="kpi-label">{name}</div>'
                            f'<div style="margin:8px 0">{pill("AT RISK", "p-critical") if at_risk else pill("PASS", "p-good")}</div>'
                            f'<div class="kpi-sub">{text}</div>', unsafe_allow_html=True)
    with cols[3]:
        kpi("Unmasked rows in Silver", f"{leaked}", f"of {total} customers")
    if blocked:
        st.error("Publishing halted: " + ", ".join(f"{b['node']} ({b['reason']})" for b in blocked))
    with st.container(border=True):
        st.markdown('<div class="section">Customer data as stored in Silver (latest 10)</div>', unsafe_allow_html=True)
        st.dataframe(data.df(conn, "SELECT customer_id, name, phone, pan, licence_no, city, masked FROM silver_customer "
                                   "ORDER BY updated_at DESC LIMIT 10"), use_container_width=True, hide_index=True)


def sla_page():
    rows_ = []
    now = datetime.now(timezone.utc)
    for g in q("SELECT * FROM gold_refresh"):
        meta = lineage.nodes().get(g["node"], {})
        since = (now - datetime.fromisoformat(g["last_refreshed"])).total_seconds() / 60
        sla_m = meta.get("sla_minutes", 60)
        left = sla_m - since
        status = "Blocked" if g["blocked"] else "Breached" if left <= 0 else "At risk" if left < sla_m * 0.25 else "OK"
        rows_.append({"product": g["node"].replace("gold_", ""), "owner": meta.get("owner"), "status": status,
                      "minutes_left": round(max(0, left), 1), "pct_left": round(max(0, 100 * left / sla_m), 1),
                      "label": f"{max(0, left):.0f} of {sla_m} min · {status.lower()}"})
    c1, c2 = st.columns([3, 2])
    with c1:
        with st.container(border=True):
            st.markdown('<div class="section">SLA budget per data product</div>', unsafe_allow_html=True)
            if rows_:
                chart(charts.sla(pd.DataFrame(rows_)))
            else:
                empty("Gold not built yet")
            st.markdown('<div class="muted">A blocked product keeps its last good copy while its SLA clock keeps running.</div>',
                        unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            st.markdown('<div class="section">Blast radius explorer</div>', unsafe_allow_html=True)
            node = st.selectbox("If this step fails…", list(lineage.nodes()), key="lin_node")
            down = [d for d in lineage.downstream(node)]
            gold = [d for d in down if d.startswith("gold_")]
            st.markdown(f'<div class="kpi-value">{len(gold)}</div><div class="kpi-sub">data products affected · '
                        f'{len(down)} downstream steps</div>', unsafe_allow_html=True)
            st.markdown("".join(f'<div class="event">{pill("gold", "p-medium")}{g} · '
                                f'<span class="muted">{lineage.nodes()[g].get("owner")}</span></div>' for g in gold)
                        or '<div class="muted">Nothing downstream.</div>', unsafe_allow_html=True)


def incidents_page():
    tl = data.incidents_timeline(conn)
    if tl.empty:
        empty("No incidents yet. Inject a problem from the sidebar.")
        return
    with st.container(border=True):
        st.markdown('<div class="section">Incident timeline</div>', unsafe_allow_html=True)
        chart(charts.incident_timeline(tl))

    incidents = q("SELECT * FROM incidents ORDER BY opened_at DESC")
    labels = {f"{i['incident_id']} · {STATE_PILL.get(i['status'], (i['status'],))[0]} · {i['title'][:80]}": i
              for i in incidents}
    inc = labels[st.selectbox("Incident", list(labels), key="inc_pick")]
    label, cls = STATE_PILL.get(inc["status"], (inc["status"], "p-info"))
    st.markdown(f'{pill(inc["severity"], SEV_PILL.get(inc["severity"], "p-info"))}{pill(label, cls)}'
                f'{pill("Gemini" if inc["llm_used"] else "rules", "p-info")}'
                f'<span class="muted">opened {local(inc["opened_at"])} · source {inc["source"]}</span>',
                unsafe_allow_html=True)
    st.markdown(f"#### {inc['incident_id']}: {inc['title']}")

    if inc["status"] == "INVESTIGATING" or not inc["root_cause"]:
        st.info("Agents are investigating…")
    else:
        c1, c2 = st.columns([3, 2])
        with c1:
            with st.container(border=True):
                st.markdown('<div class="section">AI root cause analysis</div>', unsafe_allow_html=True)
                conf = inc["confidence"] or 0
                st.markdown(f'<div class="ai-box">{inc["root_cause"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="muted" style="margin-top:8px">Category <b>{inc["category"]}</b> · confidence</div>',
                            unsafe_allow_html=True)
                st.progress(conf, text=f"{conf:.0%}")
                evidence = json.loads(inc["evidence"] or "[]")
                if evidence:
                    st.markdown('<div class="section" style="margin-top:8px">Evidence</div>' +
                                "".join(f'<div class="event">› {e}</div>' for e in evidence), unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown('<div class="section">Proposed fix</div>', unsafe_allow_html=True)
                st.write(inc["proposed_fix"])
                if inc["status"] == "AWAITING_APPROVAL":
                    st.markdown('<div class="muted">Runs only after approval, tested on a small sample first.</div>',
                                unsafe_allow_html=True)
                    a, r, _ = st.columns([1, 1, 3])
                    if a.button("Approve fix", type="primary", key=f"ok_{inc['incident_id']}", use_container_width=True):
                        decide(conn, inc["incident_id"], True, getpass.getuser())
                        st.toast("Approved. The fix runs on the next pipeline cycle.")
                    if r.button("Reject", key=f"no_{inc['incident_id']}", use_container_width=True):
                        decide(conn, inc["incident_id"], False, getpass.getuser())
                        st.toast("Rejected. Marked for manual fixing.")
                elif inc["test_result"]:
                    st.markdown(f'<div class="muted">Result: {inc["test_result"]}</div>', unsafe_allow_html=True)
        with c2:
            with st.container(border=True):
                impact = json.loads(inc["impact"] or "{}")
                st.markdown('<div class="section">Impact</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="muted">{impact.get("summary", "")}</div>', unsafe_allow_html=True)
                live = {r["node"]: r for r in q("SELECT * FROM gold_refresh")}
                for g in impact.get("gold", []):
                    r = live.get(g["node"], {})
                    left = g["sla_minutes"] - (datetime.now(timezone.utc) - datetime.fromisoformat(r["last_refreshed"])
                                               ).total_seconds() / 60 if r else g["minutes_left"]
                    tag = pill("blocked", "p-critical") if r.get("blocked") else pill("flowing", "p-good")
                    st.markdown(f'<div class="event">{tag}<b>{g["node"].replace("gold_", "")}</b> · '
                                f'<span class="muted">{g["owner"]} · {max(0, left):.0f} min SLA left</span></div>',
                                unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown('<div class="section">Activity</div>', unsafe_allow_html=True)
                for e in q("SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (inc["incident_id"],)):
                    st.markdown(f'<div class="event"><code>{local(e["ts"])}</code> <b>{e["actor"]}</b> · {e["message"]}</div>',
                                unsafe_allow_html=True)
    if inc["report"]:
        with st.expander("Final incident report", expanded=inc["status"] == "CLOSED"):
            st.markdown(inc["report"])


@st.fragment(run_every="3s")
def body():
    if not q("SELECT 1 FROM pipeline_runs LIMIT 1"):
        st.info("No pipeline runs yet. Start `python run_all.py` and `python run_pipeline.py` in two other terminals.")
        return
    k = data.kpis(conn)
    header(k)
    tabs = st.tabs(["Overview", "Service map", "Data quality", "Compliance", "SLA & lineage", "Incidents"])
    with tabs[0]:
        overview(k)
    with tabs[1]:
        service_map()
    with tabs[2]:
        quality_page()
    with tabs[3]:
        compliance_page()
    with tabs[4]:
        sla_page()
    with tabs[5]:
        incidents_page()


body()
