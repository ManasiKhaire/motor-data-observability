"""Run ONE source generator. Each team member runs their own.

Examples
  python run_generator.py --source claims
  python run_generator.py --source claims --inject negative_amount
  python run_generator.py --source telematics --batches 3 --interval 1
  python run_generator.py --source customer_kyc --out /Volumes/main/motor/landing
  python run_generator.py --list
"""
import argparse
from pathlib import Path

from common.config import ROOT, load_settings
from common.reference import build_reference
from generators import GENERATORS


def main():
    parser = argparse.ArgumentParser(description="Motor insurance data generator")
    parser.add_argument("--source", choices=GENERATORS, help="Which source to generate")
    parser.add_argument("--inject", help="Problem to inject from the start (see --list)")
    parser.add_argument("--batches", type=int, default=0, help="Number of batches; 0 = run until Ctrl+C")
    parser.add_argument("--interval", type=float, help="Seconds between batches (overrides settings.yaml)")
    parser.add_argument("--out", help="Output folder (default: output_dir in settings.yaml)")
    parser.add_argument("--list", action="store_true", help="Show every source and its problems")
    args = parser.parse_args()

    if args.list or not args.source:
        for name, cls in GENERATORS.items():
            print(f"\n{name}")
            for problem, text in cls.problems.items():
                print(f"  {problem:20s} {text}")
        if not args.source:
            print("\nPick one with --source <name>")
        return

    settings = load_settings()
    out_dir = Path(args.out) if args.out else ROOT / settings["output_dir"]
    generator = GENERATORS[args.source](settings, build_reference(settings), out_dir)
    generator.run(batches=args.batches, inject=args.inject, interval=args.interval)


if __name__ == "__main__":
    main()
