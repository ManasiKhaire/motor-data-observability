"""Impact agent: follows lineage downstream to every affected Gold table and counts down to its SLA."""
from datetime import datetime, timezone

from pipeline import lineage
from pipeline.db import rows


def assess(conn, alerts):
    affected = []
    for step in sorted({a["step"] for a in alerts}):
        for node in [step] + lineage.downstream(step):
            if node not in affected:
                affected.append(node)

    refresh = {r["node"]: r for r in rows(conn, "SELECT * FROM gold_refresh")}
    now = datetime.now(timezone.utc)
    gold = []
    for node in affected:
        meta = lineage.nodes().get(node, {})
        if meta.get("layer") != "gold":
            continue
        r = refresh.get(node, {})
        blocked = bool(r.get("blocked"))
        sla = meta.get("sla_minutes", 60)
        since = 0.0
        if r.get("last_refreshed"):
            since = (now - datetime.fromisoformat(r["last_refreshed"])).total_seconds() / 60
        gold.append({"node": node, "owner": meta.get("owner", "-"), "sla_minutes": sla,
                     "minutes_since_refresh": round(since, 1), "minutes_left": round(sla - since, 1),
                     "blocked": blocked, "regulation": meta.get("regulation", [])})

    blocked = [g for g in gold if g["blocked"]]
    if blocked:
        worst = min(blocked, key=lambda g: g["minutes_left"])
        summary = (f"{len(gold)} data products affected; {len(blocked)} blocked. "
                   f"{worst['node']} ({worst['owner']}) breaches its SLA in {max(0, worst['minutes_left']):.0f} min.")
    elif gold:
        summary = (f"{len(gold)} data products receive incomplete data: "
                   + ", ".join(g["node"].replace("gold_", "") for g in gold) + ". None blocked yet.")
    else:
        summary = "No data products affected."
    return {"affected_nodes": affected, "gold": gold, "summary": summary}
