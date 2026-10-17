# Databricks notebook source
# MAGIC %md
# MAGIC Shared setup, pulled into every pipeline notebook with `%run ./_bootstrap`.
# MAGIC Puts the repo root on the Python path (so `lakehouse.pipeline` imports work in a
# MAGIC Git folder) and reads the `catalog` parameter.

# COMMAND ----------

import os
import sys


def _find_repo_root() -> str:
    path = os.getcwd()
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "lakehouse", "pipeline")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("Could not find the repo root. Run these notebooks from the repo's Git folder.")


REPO_ROOT = _find_repo_root()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from lakehouse.pipeline import config, contracts, sql_runner  # noqa: E402

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog")  # noqa: F821 - dbutils exists in notebooks
CATALOG = dbutils.widgets.get("catalog").strip() or config.DEFAULT_CATALOG  # noqa: F821
SCHEMAS = config.Schemas(CATALOG)
SOURCE_DATA_DIR = os.path.join(REPO_ROOT, "lakehouse", "source_data")

print(f"Repo root: {REPO_ROOT}")
print(f"Catalog:   {CATALOG}  (schemas: bronze, ref, silver, gold)")
