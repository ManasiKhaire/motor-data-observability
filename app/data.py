"""Queries behind the dashboard. Everything returns pandas DataFrames ready for charts."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from pipeline import lineage

SEV_WEIGHT = {"CRITICAL": 25, "HIGH": 12, "MEDIUM": 5, "LOW": 2}


def df(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=cols)


def since_iso(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def bucket(ts_series, seconds):
    t = pd.to_datetime(ts_series, utc=True, format="ISO8601")
    return t.dt.floor(f"{seconds}s")


def _trim_edges(b):
    """Drop the first and last time buckets: they are only partly filled and dip misleadingly."""
    times = sorted(b["time"].unique())
    if len(times) > 3:
        b = b[(b["time"] > times[0]) & (b["time"] < times[-1])]
    return b


def batches(conn, minutes):
    """One row per landing file: rows in, rows stopped, time."""
    b = df(conn, """
        SELECT f.source, f.file, f.rows, f.ingested_at,
               COALESCE(q.bad, 0) AS bad
        FROM file_registry f
        LEFT JOIN (SELECT batch_file, COUNT(*) AS bad FROM quarantine GROUP BY batch_file) q
               ON q.batch_file = f.file
        WHERE f.ingested_at >= ?""", (since_iso(minutes),))
    if not b.empty:
        b["good"] = (b["rows"] - b["bad"]).clip(lower=0)
    return b


def throughput(conn, minutes=15, seconds=20):
    b = batches(conn, minutes)
    if b.empty:
        return b
    b["time"] = bucket(b["ingested_at"], seconds)
    b = _trim_edges(b)
    out = b.groupby(["time", "source"], as_index=False)["rows"].sum()
    # A silent source must show as 0, not as a line drawn straight across the gap.
    grid = pd.MultiIndex.from_product([sorted(out["time"].unique()), sorted(out["source"].unique())],
                                      names=["time", "source"]).to_frame(index=False)
    out = grid.merge(out, how="left", on=["time", "source"]).fillna({"rows": 0})
    out["rows_per_min"] = out["rows"] * 60 / seconds
    return out


def quality_trend(conn, minutes=15, seconds=30):
    """Share of rows that passed every check, per time bucket."""
    b = batches(conn, minutes)
    if b.empty:
        return b
    b["time"] = bucket(b["ingested_at"], seconds)
    b = _trim_edges(b)
    g = b.groupby("time", as_index=False)[["rows", "good"]].sum()
    g["pass_rate"] = (100 * g["good"] / g["rows"]).round(1)
    return g


def alert_events(conn, minutes=30, seconds=60):
    a = df(conn, "SELECT severity, source, first_seen FROM alerts WHERE first_seen >= ?", (since_iso(minutes),))
    if a.empty:
        return a
    a["time"] = bucket(a["first_seen"], seconds)
    return a.groupby(["time", "severity"], as_index=False).size().rename(columns={"size": "alerts"})


def failure_matrix(conn, minutes=15):
    q = df(conn, "SELECT source, check_name, COUNT(*) AS rows FROM quarantine WHERE quarantined_at >= ? "
                 "GROUP BY source, check_name", (since_iso(minutes),))
    fresh = df(conn, "SELECT source, check_name, occurrences AS rows FROM alerts "
                     "WHERE check_name IN ('freshness', 'pii') AND last_seen >= ?", (since_iso(minutes),))
    return pd.concat([q, fresh], ignore_index=True)


def freshness(conn, settings):
    last = df(conn, "SELECT source, MAX(ingested_at) AS last FROM file_registry GROUP BY source")
    if last.empty:
        return last
    now = datetime.now(timezone.utc)
    factor = settings["pipeline"]["freshness_factor"]
    last["silent_s"] = [(now - datetime.fromisoformat(t)).total_seconds() for t in last["last"]]
    last["limit_s"] = [settings["sources"][s]["interval_seconds"] * factor for s in last["source"]]
    last["pct_of_limit"] = (100 * last["silent_s"] / last["limit_s"]).round(0)
    last["bar_pct"] = last["pct_of_limit"].clip(upper=200)  # keep a stopped feed on the chart
    last["label"] = [f"{s:.0f}s" + (" · stopped" if p > 200 else "") for s, p in zip(last["silent_s"], last["pct_of_limit"])]
    last["state"] = pd.cut(last["pct_of_limit"], [-1, 60, 100, 1e9], labels=["Fresh", "Late", "Stale"]).astype(str)
    return last


def kpis(conn):
    now = datetime.now(timezone.utc)
    open_alerts = df(conn, "SELECT severity FROM alerts WHERE status='OPEN'")
    blocked = conn.execute("SELECT COUNT(*) FROM gold_refresh WHERE blocked=1").fetchone()[0]
    penalty = sum(SEV_WEIGHT.get(s, 2) for s in open_alerts["severity"]) + 10 * blocked
    inc = df(conn, "SELECT status, opened_at, closed_at, llm_used FROM incidents")
    closed = inc[(inc["status"] == "CLOSED") & inc["closed_at"].notna()] if not inc.empty else inc
    mttr = None
    if not closed.empty:
        mttr = ((pd.to_datetime(closed["closed_at"], format="ISO8601") -
                 pd.to_datetime(closed["opened_at"], format="ISO8601")).dt.total_seconds() / 60).mean()
    last5 = batches(conn, 5)
    rows_min = (last5["rows"].sum() / 5) if not last5.empty else 0
    pass_rate = (100 * last5["good"].sum() / last5["rows"].sum()) if not last5.empty and last5["rows"].sum() else None
    total = conn.execute("SELECT COALESCE(SUM(rows), 0) FROM file_registry").fetchone()[0]
    return {
        "health": max(0, 100 - penalty), "open_alerts": len(open_alerts),
        "critical": int((open_alerts["severity"] == "CRITICAL").sum()) if not open_alerts.empty else 0,
        "active_incidents": int((inc["status"] != "CLOSED").sum()) if not inc.empty else 0,
        "waiting": int((inc["status"] == "AWAITING_APPROVAL").sum()) if not inc.empty else 0,
        "mttr": mttr, "rows_per_min": rows_min, "pass_rate": pass_rate, "total_rows": total,
        "blocked": blocked, "ai_incidents": int(inc["llm_used"].sum()) if not inc.empty else 0,
        "now": now,
    }


def incidents_timeline(conn):
    inc = df(conn, "SELECT incident_id, source, status, severity, title, opened_at, closed_at, category "
                   "FROM incidents ORDER BY opened_at")
    if inc.empty:
        return inc
    now = datetime.now(timezone.utc).isoformat()
    inc["start"] = pd.to_datetime(inc["opened_at"], format="ISO8601")
    inc["end"] = pd.to_datetime(inc["closed_at"].fillna(now), format="ISO8601")
    inc["minutes"] = ((inc["end"] - inc["start"]).dt.total_seconds() / 60).round(1)
    inc["state"] = inc["status"].map(lambda s: "Resolved" if s == "CLOSED" else
                                     "Awaiting approval" if s == "AWAITING_APPROVAL" else "Open")
    return inc


def node_metrics(conn, minutes=5):
    """Rows per minute through each lineage node, for the service map labels."""
    b = batches(conn, minutes)
    per_source = b.groupby("source")["rows"].sum() / minutes if not b.empty else pd.Series(dtype=float)
    good = b.groupby("source")["good"].sum() / minutes if not b.empty else pd.Series(dtype=float)
    metrics = {}
    for node, meta in lineage.nodes().items():
        src = next((s for s in per_source.index if node.endswith(s) or (s == "customer_kyc" and node in
                    ("mask_pii", "silver_customer"))), None)
        if meta["layer"] in ("source", "bronze") and src:
            metrics[node] = f"{per_source[src]:.0f} rows/min"
        elif meta["layer"] in ("silver", "step") and src:
            metrics[node] = f"{good.get(src, 0):.0f} rows/min"
    for g in df(conn, "SELECT node, blocked, last_refreshed FROM gold_refresh").to_dict("records"):
        if g["blocked"]:
            metrics[g["node"]] = "publishing halted"
        else:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(g["last_refreshed"])).total_seconds()
            metrics[g["node"]] = f"refreshed {age:.0f}s ago"
    return metrics
