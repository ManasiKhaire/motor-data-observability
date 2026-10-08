"""Writing batch files, log lines and reading the live injection switch.

Folder layout under the output directory:
  landing/<source>/   new data files, picked up by Auto Loader
  logs/<source>.log   one JSON line per event, read by the root cause agent
  control/            inject.json (live failure switch) and vault_token.json
"""
import csv
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot serialise {type(value)}")


def write_batch(out_dir: Path, source: str, records: list, fmt: str, batch_no: int) -> Path:
    """Write one batch atomically: a temp file first, then rename.

    The temp name starts with "_", which Spark and Auto Loader ignore,
    so a half-written file is never read.
    """
    folder = Path(out_dir) / "landing" / source
    folder.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%f")
    name = f"{source}_{stamp}_{batch_no:06d}.{'json' if fmt == 'json' else 'csv'}"
    tmp, final = folder / f"_{name}", folder / name

    if fmt == "json":
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, default=_default) + "\n")
    else:
        columns = []
        for r in records:  # union of keys keeps extra columns from schema drift
            for k in r:
                if k not in columns:
                    columns.append(k)
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for r in records:
                writer.writerow({k: (_default(v) if isinstance(v, (date, datetime)) else v)
                                 for k, v in r.items()})
    os.replace(tmp, final)
    return final


def log_event(out_dir: Path, source: str, level: str, message: str, **extra) -> None:
    folder = Path(out_dir) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    line = {"ts": utc_now().isoformat(), "source": source, "level": level,
            "message": message, **extra}
    with open(folder / f"{source}.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=_default) + "\n")


def _control_file(out_dir: Path, name: str) -> Path:
    folder = Path(out_dir) / "control"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    tmp = path.with_name(f"_{path.name}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_default)
    os.replace(tmp, path)


def get_injection(out_dir: Path, source: str):
    data = read_json(_control_file(out_dir, "inject.json"), {})
    value = data.get(source)
    return None if value in (None, "", "off") else value


def set_injection(out_dir: Path, source: str, problem) -> None:
    path = _control_file(out_dir, "inject.json")
    data = read_json(path, {})
    data[source] = problem or "off"
    write_json(path, data)


def set_vault_token(out_dir: Path, status: str) -> None:
    write_json(_control_file(out_dir, "vault_token.json"),
               {"status": status, "updated_at": utc_now().isoformat()})
