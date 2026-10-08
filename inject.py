"""Switch a problem on or off while generators are running (no restart needed).

  python inject.py claims negative_amount     switch on
  python inject.py claims off                 switch off
  python inject.py status                     show what is on now
"""
import argparse
from pathlib import Path

from common.config import ROOT, load_settings
from common.io import read_json, set_injection
from generators import GENERATORS


def main():
    parser = argparse.ArgumentParser(description="Live problem switch for the demo")
    parser.add_argument("source", help="Source name, or 'status'")
    parser.add_argument("problem", nargs="?", help="Problem name, or 'off'")
    parser.add_argument("--out", help="Output folder")
    args = parser.parse_args()

    settings = load_settings()
    out_dir = Path(args.out) if args.out else ROOT / settings["output_dir"]

    if args.source == "status":
        data = read_json(out_dir / "control" / "inject.json", {})
        token = read_json(out_dir / "control" / "vault_token.json", {"status": "valid"})
        for name in GENERATORS:
            print(f"{name:14s} {data.get(name, 'off')}")
        print(f"{'vault token':14s} {token.get('status')}")
        return

    if args.source not in GENERATORS:
        parser.error(f"unknown source '{args.source}', choose from {', '.join(GENERATORS)}")
    valid = GENERATORS[args.source].problems
    if args.problem not in valid and args.problem != "off":
        parser.error(f"problem must be 'off' or one of: {', '.join(valid)}")
    set_injection(out_dir, args.source, args.problem)
    print(f"{args.source}: {args.problem}")


if __name__ == "__main__":
    main()
