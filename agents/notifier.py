"""Tells the owner. Always writes to output/notifications.log; also posts to Teams if a webhook is set."""
import json
import os
import urllib.request
from pathlib import Path

from common.io import log_event, utc_now


def notify(out_dir, incident_id, title, message):
    out_dir = Path(out_dir)
    line = f"[{utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC] {incident_id} | {title}\n{message}\n"
    with open(out_dir / "notifications.log", "a", encoding="utf-8") as f:
        f.write(line + "-" * 60 + "\n")
    print(f"\n>>> NOTIFY {incident_id}: {title}\n{message}\n")

    url = os.environ.get("TEAMS_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": f"**{incident_id}: {title}**\n\n{message}"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # a blocked webhook must never stop the pipeline
        log_event(out_dir, "pipeline", "WARN", "Teams webhook failed", detail=str(e)[:200])
