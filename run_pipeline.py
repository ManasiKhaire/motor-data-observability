"""Run the pipeline and the AI agents in a loop (Ctrl+C to stop).

  python run_pipeline.py              every few seconds, forever
  python run_pipeline.py --cycles 3   a fixed number of cycles
  python run_pipeline.py --reset      start from an empty database (keeps landing files)
"""
import argparse
import time

from agents.llm import GeminiClient
from agents.orchestrator import Agents
from common.config import database_path, load_settings, output_dir
from pipeline.db import connect
from pipeline.runner import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Pipeline + checks + AI agents")
    parser.add_argument("--cycles", type=int, default=0, help="0 = run until Ctrl+C")
    parser.add_argument("--interval", type=float, help="Seconds between cycles")
    parser.add_argument("--out", help="Output folder")
    parser.add_argument("--reset", action="store_true", help="Delete the database first")
    args = parser.parse_args()

    settings = load_settings()
    out_dir = output_dir(settings, args.out)
    db = database_path(out_dir)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            p = db.with_name(db.name + suffix)
            if p.exists():
                p.unlink()
    conn = connect(db)
    llm = GeminiClient(settings, out_dir)
    pipeline, agents = Pipeline(conn, settings, out_dir), Agents(conn, settings, out_dir, llm)
    wait = args.interval if args.interval is not None else settings["pipeline"]["interval_seconds"]
    print(f"Pipeline running every {wait}s | database {db} | LLM: {llm.status}  (Ctrl+C to stop)")

    done = 0
    try:
        while args.cycles == 0 or done < args.cycles:
            stats = pipeline.run_cycle(agents)
            open_alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='OPEN'").fetchone()[0]
            waiting = conn.execute("SELECT COUNT(*) FROM incidents WHERE status='AWAITING_APPROVAL'").fetchone()[0]
            print(f"cycle {done + 1}: {stats['files']} files, {stats['rows']} rows | "
                  f"open alerts {open_alerts} | awaiting approval {waiting}")
            done += 1
            if args.cycles == 0 or done < args.cycles:
                time.sleep(wait)
    except KeyboardInterrupt:
        print("\nPipeline stopped")


if __name__ == "__main__":
    main()
