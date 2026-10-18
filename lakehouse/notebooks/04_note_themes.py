# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Note themes: tag free-text notes so themes can be counted (planned)
# MAGIC
# MAGIC Same job as tagging warranty or complaint notes by failure mode: an LLM assigns each
# MAGIC note one label from a **fixed** list, and gold counts the labels. The LLM classifies;
# MAGIC SQL does the counting.
# MAGIC
# MAGIC ### Output contract
# MAGIC | Table | Columns |
# MAGIC |---|---|
# MAGIC | `gold.note_themes` | `source_system`, `note_id`, `drift_id`, `theme` (one of `THEMES` below), `method` (e.g. `ai_classify`), `tagged_at` |
# MAGIC | `gold.kpi_note_themes` | `theme`, `notes`, `findings`, `open_findings`, `escalated_notes` |
# MAGIC
# MAGIC ### Approach
# MAGIC 1. **Tag distinct texts, not rows.** 804 notes but only ~34 distinct texts. Classify
# MAGIC    `SELECT DISTINCT note_text`, then join the label back. Same quality, a fraction of the calls.
# MAGIC 2. **Classifier, option A (in-platform):** `ai_classify(note_text, <labels JSON>, map('version', '2.1'))`
# MAGIC    in SQL. The labels JSON can map each label to a description (see `THEMES`). Version 2.1
# MAGIC    returns a VARIANT whose `response` holds objects with a `value` key. AI Functions may
# MAGIC    not be enabled on Free Edition; if the call errors, use option B.
# MAGIC 3. **Classifier, option B (fallback):** call OpenAI from Python (`gpt-5-mini`, the model
# MAGIC    the agent already uses) with the same labels and descriptions, using a secret scope for
# MAGIC    the key (`dbutils.secrets.get(...)`), never a literal key in the notebook.
# MAGIC 4. **Measure before trusting it.** Hand-label 10 to 15 distinct texts in `GOLDEN` below and
# MAGIC    report agreement alongside the theme counts.
# MAGIC 5. Write both tables, overwrite mode (small, rebuilt each run).
# MAGIC
# MAGIC Later: embeddings + Vector Search to *discover* new themes rather than assign known ones.

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

import json

from pyspark.sql import functions as F

THEMES = {
    "vendor_dependency": "Waiting on a vendor patch, vendor review, or third-party certification.",
    "internal_dependency": "Blocked by another internal system or team: shared middleware, load balancer, client library, downstream API.",
    "testing_validation": "Results of testing or validation in QA or lower environments, pass or fail.",
    "change_window": "Scheduling: maintenance windows, CAB approval, quarter-end blackout, bundled platform refresh.",
    "exemption_risk": "Exemption requests, reviews, approvals, denials, extensions, or risk acceptance.",
    "ownership_escalation": "Escalations, aging thresholds breached, no owner response, follow-ups requested.",
    "remediated_closed": "Drift resolved: upgrade completed, version aligned, closed, or false positive.",
    "decommission": "Application is being retired or decommissioned.",
}
LABELS_JSON = json.dumps(THEMES)

notes = spark.table(f"{SCHEMAS.silver}.drift_notes")
distinct_texts = notes.select("note_text").distinct()
print(f"{notes.count()} notes, {distinct_texts.count()} distinct texts")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Classify the distinct texts
# MAGIC Produces a DataFrame `labeled` with columns `note_text`, `theme`, `method`.
# MAGIC Every `theme` must be a key of `THEMES` (the check below enforces it).

# COMMAND ----------

labeled = None  # TODO: classify with ai_classify, or the OpenAI fallback

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Measure agreement on a hand-labeled sample
# MAGIC 10 to 15 distinct texts from `distinct_texts`, each with a hand-assigned label.

# COMMAND ----------

GOLDEN: dict[str, str] = {
    # "Exemption denied; risk accepted without compensating controls is outside policy threshold.": "exemption_risk",
}


def agreement(labeled_df, golden: dict[str, str]) -> float:
    if not golden:
        raise ValueError("Add hand labels to GOLDEN first.")
    predicted = {row.note_text: row.theme for row in labeled_df.collect()}
    hits = sum(predicted.get(text) == label for text, label in golden.items())
    return hits / len(golden)


# print(f"Agreement with hand labels: {agreement(labeled, GOLDEN):.0%}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Write gold.note_themes and gold.kpi_note_themes

# COMMAND ----------

assert labeled is not None, "Classification not built yet."
bad = labeled.where(~F.col("theme").isin(list(THEMES)))
assert bad.count() == 0, f"Labels outside the fixed list: {[r.theme for r in bad.limit(5).collect()]}"

# TODO: write both tables
