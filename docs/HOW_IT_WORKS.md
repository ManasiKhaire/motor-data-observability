# How it works

This guide walks through the system in the order data moves through it. Each section names
the file to open if you want to read the code.

## 1. Live data (`generators/`)

Five Python scripts act like real source systems. Every few seconds each writes one small
file into `output/landing/<source>/`. They share one set of customers, cars and policies
(`common/reference.py`, built from a fixed seed), so IDs match across sources and joins work.

Each generator can inject its typical failure. The switch is a file,
`output/control/inject.json`, which the dashboard sidebar and `inject.py` write to. When a
problem is on, the generator also writes a realistic cause to `output/logs/<source>.log`
(for example `Vault returned 403 on token refresh`). Those log lines are the evidence the
root cause agent later reads, just as it would read real system logs.

## 2. Pipeline (`pipeline/`)

Every 5 seconds `run_pipeline.py` runs one cycle (`pipeline/runner.py`):

1. **Ingest to Bronze.** New files are stored exactly as they arrived, so nothing is lost.
2. **Checks** (`checks.py`). Each batch is compared with its contract in
   `schemas/source_contracts.json`: columns, empty values, ranges, allowed values, formats,
   duplicates, references (for example a bill must point to a real claim) and
   reconciliation (bill total = parts + labour).
3. **Quarantine.** Rows that fail stay out of Silver, in a `quarantine` table with the
   reason. This is containment: bad data never reaches the business.
4. **Silver** (`transform.py`). Good rows are typed and cleaned. Customer data is masked
   (name, phone, PAN, licence, address, date of birth). Masking needs a valid Vault token.
5. **PII scan.** Silver customer data is scanned for readable phone numbers and PAN. If
   any are found, publishing of `gold_customer_360` and `gold_compliance_report` is halted.
6. **Gold** (`gold.py`). Five data products are rebuilt from Silver: driver risk score,
   claims summary, fraud watchlist, customer 360, compliance report. A blocked one keeps its
   last good copy, and its SLA clock keeps running.
7. **Freshness.** A source that has been silent for 4x its normal interval raises an alert.
8. **Map colours.** Each lineage node becomes green, yellow or red. Anything downstream of
   a red node turns yellow ("at risk").

Every failed check becomes a row in the `alerts` table. Repeats update the same alert
rather than creating new ones, and an alert closes itself after two clean batches.

### The deliberate bug

When the Vault token is expired, masking **fails open**: it logs the error but lets raw
rows through. Real pipelines have bugs like this, and it is exactly what the platform must
catch. One of the prevention steps in the final report is to make masking fail closed.

## 3. Agents (`agents/`)

The orchestrator moves each incident one step per cycle:

```
alerts ─> INVESTIGATING ─> AWAITING_APPROVAL ─┬─ approve ─> test ─> apply ─> verify ─> CLOSED
                                              └─ reject  ─> MANUAL ─> (alerts clear) ─> CLOSED
```

| Agent | What it does | Uses Gemini? |
|---|---|---|
| Orchestrator | Groups new alerts into one incident per source | No |
| Root cause | Collects evidence: alerts, WARN/ERROR log lines from 15 minutes before, lineage upstream, which steps need secrets. Asks for cause, category and confidence | Yes, with rules as fallback and cross-check |
| Impact | Walks lineage downstream to every Gold table; owner and minutes left on SLA | No (pure graph logic) |
| Fix | Maps the category to one playbook action | Gemini only words the advice |
| Actions | Test on a small sample, apply, verify | No |
| Report | Writes the closing report | Yes, with a template fallback |
| Notifier | Writes `output/notifications.log`; posts to Teams if a webhook is set | No |

### Why the LLM can't break anything

- It only sees a small JSON summary, never the database or files.
- Its answer is validated: an unknown category is ignored and the rules decide.
- It never chooses what code runs. Actions come from a fixed playbook
  (`fixer.PLAYBOOK`), and only after a person approves.

### The fix playbook

| Category | Action | Test on sample | Apply |
|---|---|---|---|
| expired_credential | renew_vault_token | Re-mask 20 leaked rows, PII scan must be clean | Renew token, re-mask all leaked rows, unblock Gold |
| schema_change | add_column_mapping | Map 20 quarantined rows, they must pass the contract | Save mapping, reload quarantined rows; future files load automatically |
| source_outage, delayed_delivery | restart_connector | Connection check | Restart (in the demo: switch the problem off) |
| bad values, duplicates, references | quarantine_and_notify_source | Confirm bad rows are only in quarantine | Notify the source owner (demo: switch the problem off) |

## 4. Dashboard (`app/dashboard.py`)

The dashboard reads the same SQLite database and refreshes every 3 seconds. Approve and
Reject write the decision to the `incidents` table; the pipeline picks it up on its next
cycle.

## 5. Mapping to Databricks

| Here (laptop) | On Databricks |
|---|---|
| `output/landing/` folders | Unity Catalog Volume |
| `file_registry` + reading new files | Auto Loader (`databricks/01_bronze_autoloader.py`) |
| SQLite tables | Delta tables in `main.motor_obs` |
| `run_pipeline.py` loop | A Databricks Job, or Structured Streaming |
| Contract checks | Same code, or DLT expectations / Lakehouse Monitoring |
| `lineage.json` | Unity Catalog lineage |
| Streamlit dashboard | Databricks Apps |
| Gemini via REST | Same, or Databricks model serving |

## 6. How this compares to Dynatrace

| Dynatrace | This project |
|---|---|
| OneAgent collects telemetry | Pipeline records every file, row, check and log line |
| Grail stores it | SQLite / Delta tables (`alerts`, `incidents`, logs) |
| Smartscape dependency map | `lineage.json` + pipeline map |
| Davis AI root cause | Root cause agent (Gemini + rules) |
| Workflows automate fixes | Fix playbook with human approval |

The difference: Dynatrace mainly watches apps and servers; this watches the **data itself**.

## 7. Ideas to extend it

- Add a new check: put the rule in `schemas/source_contracts.json` or `checks._source_rules`.
- Add a new problem: add it to a generator's `problems` and handle it in `make_batch`.
- Add a playbook action: add the category in `root_cause.CATEGORIES`, map it in
  `fixer.PLAYBOOK`, implement `test` and `apply` in `actions.py`.
- Swap the LLM: only `agents/llm.py` talks to Gemini.
