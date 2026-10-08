"""Approve or reject a proposed fix from the terminal (the dashboard has buttons too).

  python approve.py                  list incidents waiting for approval
  python approve.py INC-0001         approve
  python approve.py INC-0001 --reject
"""
import argparse
import getpass

from agents.orchestrator import decide
from common.config import database_path, load_settings, output_dir
from pipeline.db import connect, rows


def main():
    parser = argparse.ArgumentParser(description="Human approval for proposed fixes")
    parser.add_argument("incident", nargs="?")
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--out", help="Output folder")
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(database_path(output_dir(settings, args.out)))
    if not args.incident:
        waiting = rows(conn, "SELECT incident_id, title, root_cause, proposed_fix FROM incidents "
                             "WHERE status='AWAITING_APPROVAL' ORDER BY opened_at")
        if not waiting:
            print("Nothing is waiting for approval.")
        for w in waiting:
            print(f"\n{w['incident_id']}  {w['title']}\n  Cause: {w['root_cause']}\n  Fix:   {w['proposed_fix']}")
        return
    ok = decide(conn, args.incident, not args.reject, getpass.getuser())
    print(f"{args.incident}: {'rejected' if args.reject else 'approved'}" if ok
          else f"{args.incident} is not waiting for approval.")


if __name__ == "__main__":
    main()
