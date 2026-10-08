"""Run all five generators together, each on its own schedule (for demos and testing).

  python run_all.py
  python run_all.py --batches 5 --interval 1     quick test run
"""
import argparse
import threading
from pathlib import Path

from common.config import ROOT, load_settings
from common.reference import build_reference
from generators import GENERATORS


def main():
    parser = argparse.ArgumentParser(description="Run every source generator at once")
    parser.add_argument("--batches", type=int, default=0, help="Batches per source; 0 = until Ctrl+C")
    parser.add_argument("--interval", type=float, help="Same interval for every source (seconds)")
    parser.add_argument("--out", help="Output folder")
    args = parser.parse_args()

    settings = load_settings()
    out_dir = Path(args.out) if args.out else ROOT / settings["output_dir"]
    ref = build_reference(settings)

    threads = []
    for name, cls in GENERATORS.items():
        gen = cls(settings, ref, out_dir)
        t = threading.Thread(target=gen.run, kwargs={"batches": args.batches, "interval": args.interval},
                             name=name, daemon=True)
        t.start()
        threads.append(t)
    try:
        for t in threads:
            while t.is_alive():
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping all generators")


if __name__ == "__main__":
    main()
