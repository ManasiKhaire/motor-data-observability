# Databricks notebook source
# MAGIC %md
# MAGIC # Run a generator inside Databricks
# MAGIC Writes files into a Unity Catalog Volume so Auto Loader (notebook 01) can read them.
# MAGIC 1. Add this repo to your workspace (Workspace > Create > Git folder).
# MAGIC 2. Create the volume once: `CREATE VOLUME IF NOT EXISTS main.motor_obs.landing;`
# MAGIC 3. Pick your source below and run all cells. Stop the last cell to stop generating.

# COMMAND ----------

# MAGIC %pip install -q Faker==30.8.2 PyYAML==6.0.2

# COMMAND ----------

dbutils.widgets.dropdown("source", "claims",
                         ["telematics", "policy", "claims", "garage_bills", "customer_kyc"])
dbutils.widgets.text("out", "/Volumes/main/motor_obs/landing")
dbutils.widgets.text("inject", "")

# COMMAND ----------

import os
import sys

repo_root = os.path.dirname(os.getcwd())  # this notebook lives in <repo>/databricks
sys.path.insert(0, repo_root)

from common.config import load_settings
from common.reference import build_reference
from generators import GENERATORS

settings = load_settings()
source = dbutils.widgets.get("source")
gen = GENERATORS[source](settings, build_reference(settings), dbutils.widgets.get("out"))
gen.run(inject=dbutils.widgets.get("inject") or None)
