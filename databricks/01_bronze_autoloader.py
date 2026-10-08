# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze ingestion with Auto Loader
# MAGIC Reads new files from one source's landing folder and appends them to its Bronze Delta table.
# MAGIC Run one copy per source (set the `source` widget), or loop over all five.
# MAGIC
# MAGIC Bronze keeps data **as it arrived**: no fixes, so checks can see every problem.
# MAGIC Unexpected columns go to `_rescued_data` instead of failing the stream, which is how schema drift shows up.

# COMMAND ----------

dbutils.widgets.dropdown("source", "claims",
                         ["telematics", "policy", "claims", "garage_bills", "customer_kyc"])
dbutils.widgets.text("landing_root", "/Volumes/main/motor_obs/landing")
dbutils.widgets.text("catalog_schema", "main.motor_obs")

source = dbutils.widgets.get("source")
landing_root = dbutils.widgets.get("landing_root")
catalog_schema = dbutils.widgets.get("catalog_schema")

file_format = "csv" if source in ("policy", "garage_bills") else "json"
landing_path = f"{landing_root}/landing/{source}"
checkpoint = f"{landing_root}/_checkpoints/bronze_{source}"
target = f"{catalog_schema}.bronze_{source}"

# COMMAND ----------

from pyspark.sql import functions as F

reader = (spark.readStream.format("cloudFiles")
          .option("cloudFiles.format", file_format)
          .option("cloudFiles.schemaLocation", checkpoint)
          .option("cloudFiles.inferColumnTypes", "true")
          .option("cloudFiles.schemaEvolutionMode", "rescue"))
if file_format == "csv":
    reader = reader.option("header", "true")

bronze = (reader.load(landing_path)
          .withColumn("_ingested_at", F.current_timestamp())
          .withColumn("_source_file", F.col("_metadata.file_path")))

(bronze.writeStream
    .option("checkpointLocation", checkpoint)
    .trigger(processingTime="10 seconds")
    .toTable(target))

# COMMAND ----------

# MAGIC %md
# MAGIC Quick look while it runs:
# MAGIC ```sql
# MAGIC SELECT * FROM main.motor_obs.bronze_claims ORDER BY _ingested_at DESC LIMIT 20;
# MAGIC ```
