# Motor Data Observability

Simulated live data for our agentic data observability project, set in **motor insurance**.
Five Python generators write new files every few seconds, exactly like real source systems
would, and each one can **inject its typical failure on demand** so the checks and AI agents
have something real to catch during the demo.

**Each person writes one generator.** The shared parts (master data, file writing, the run
loop, the live problem switch, tests) are already done. Your file in `generators/` is a
template with the fields, rules and problems listed and `TODO`s to fill in.

## Your task

1. Open your file in `generators/` and read its docstring.
2. Fill in `make_batch(self, n, problem)`: return a list of `n` dicts (one per record),
   using IDs from `self.ref` and randomness from `self.rng`.
3. Handle each problem in your `problems` list, and `self.log("ERROR", "...")` a realistic
   cause, so the root cause agent has evidence to find.
4. Run `python -m pytest -q`: your source's tests switch from *skipped* to *passed* when your
   columns match `schemas/source_contracts.json` and your IDs match the shared master data.
5. Run it: `python run_generator.py --source <yours> --batches 3 --interval 1`.

What you can use inside `make_batch`:

| Name | What it gives you |
|---|---|
| `self.ref.customers` / `vehicles` / `policies` / `devices` / `garages` | Shared master data (lists of dicts) |
| `self.ref.active_policies` / `expired_policies` | Policies split by end date |
| `self.rng` | Random number generator (`self.rng.choice`, `.sample`, `.uniform` ...) |
| `self.log(level, message, **extra)` | Writes a line to `output/logs/<source>.log` |
| `self.out_dir` | Output folder (garage bills reads claims files from here) |
| `common.io.utc_now()` | Current time (UTC) |
| `common.io.set_vault_token(out_dir, status)` | KYC only: `"valid"` or `"expired"` |

## The five sources

| # | Source | Owner | Format | Every | Problems you can inject |
|---|---|---|---|---|---|
| 1 | `telematics` | Person 1 | JSON | 5 s | `feed_stop`, `duplicates`, `impossible_values` |
| 2 | `policy` | Person 2 | CSV | 60 s | `schema_drift`, `bad_dates`, `missing_vehicle_id` |
| 3 | `customer_kyc` | Person 3 | JSON | 30 s | `expired_token`, `duplicate_customer`, `invalid_pan` |
| 4 | `claims` | Person 4 | JSON | 10 s | `null_amount`, `negative_amount`, `duplicate_claim`, `expired_policy` |
| 5 | `garage_bills` | Person 5 | CSV | 30 s | `total_mismatch`, `orphan_claim`, `late` |

All sources share the same customer, policy and vehicle IDs (same seed), so joins work:

```
customer_kyc (customer_id) -> policy (policy_id, vehicle_id) -> claims (claim_id) -> garage_bills (bill_id)
                                          ^
                              telematics (vehicle_id, policy_id)
```

## Quick start (laptop)

```bash
git clone https://github.com/ManasiKhaire/motor-data-observability.git
cd motor-data-observability
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run_generator.py --list                          # every source and its problems
python run_generator.py --source claims                 # run your own source (Ctrl+C to stop)
```

Run everything at once (for demos):

```bash
python run_all.py
python run_all.py --batches 5 --interval 1              # quick 5-second test
```

## Injecting problems

From the start:

```bash
python run_generator.py --source claims --inject negative_amount
```

Or **live, while generators are running**, from a second terminal (best for the demo):

```bash
python inject.py claims negative_amount      # switch on
python inject.py claims off                  # switch off
python inject.py status                      # what is on right now
```

## What gets written

```
output/
  landing/<source>/   data files, one per batch   <- Auto Loader reads these
  logs/<source>.log   one JSON line per event     <- the root cause agent reads these
  control/inject.json      live problem switch
  control/vault_token.json valid | expired        <- the PII masking step reads this
```

Files are written to a temp name starting with `_` and then renamed, so Spark never reads half a file.

### The expired-token story (main demo)

`customer_kyc` sends **raw** personal data, as real source systems do. Masking happens in the
pipeline (Bronze to Silver) and needs a valid Vault token. `expired_token` sets
`control/vault_token.json` to `expired` and logs `Vault returned 403 on token refresh`.
The masking step should read that file: if the token is expired, masking fails, raw PII
reaches Silver, the PII scan raises an alert, and the agents trace it back to the token.

## On Databricks

1. Workspace > Create > **Git folder**, paste this repo URL.
2. Run once in SQL: `CREATE SCHEMA IF NOT EXISTS main.motor_obs; CREATE VOLUME IF NOT EXISTS main.motor_obs.landing;`
   then the statements in `sql/alerts_table.sql`.
3. `databricks/00_run_generator.py`: pick your source, run all. Files go to `/Volumes/main/motor_obs/landing`.
4. `databricks/01_bronze_autoloader.py`: pick the same source, run all. New files stream into `main.motor_obs.bronze_<source>`.

Change `main.motor_obs` if your workspace uses another catalog or schema.

## Repository map

| Path | What it is |
|---|---|
| `config/settings.yaml` | Seed, output folder, rows per batch and interval for each source |
| `common/reference.py` | Shared master data (customers, vehicles, policies, devices, garages) built from the seed |
| `common/io.py` | Writes batch files and log lines, reads and sets the live problem switch |
| `generators/base.py` | The loop every generator shares |
| `generators/<source>.py` | One file per source, **written by its owner** (template with TODOs) |
| `run_generator.py` | Run one source |
| `run_all.py` | Run all five together |
| `inject.py` | Switch problems on and off live |
| `schemas/source_contracts.json` | Expected columns, types and rules per source, for the checks |
| `lineage/lineage.json` | What feeds what, owners and SLAs, for the Impact agent |
| `sql/alerts_table.sql` | Shared `alerts` and `incidents` Delta tables |
| `databricks/` | Notebooks to run a generator and stream it into Bronze with Auto Loader |
| `tests/` | `python -m pytest -q` checks each written source against its contract; unwritten ones are skipped |

## Adding a new problem to your source

1. Add it to `problems` at the top of your generator with a one-line description.
2. Handle it in `make_batch`, and log a realistic cause with `self.log("ERROR", "...")` so the root cause agent has evidence.
3. Run `python -m pytest -q`.

## Working together

- Work on a branch named after your source (`git checkout -b claims`) and open a pull request.
- Change only your own generator file. Ask before changing `common/`, `config/` or `schemas/`, because everyone depends on them.
- Garage bills reads claim files, so Person 4 (claims) should push a working version early.
