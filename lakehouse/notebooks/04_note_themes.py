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
# MAGIC 4. **Measure before trusting it.** Hand-label 10 to 15 distinct texts and report
# MAGIC    agreement alongside the theme counts.
# MAGIC 5. Write both tables, overwrite mode (small, rebuilt each run).
# MAGIC
# MAGIC Later: embeddings + Vector Search to *discover* new themes rather than assign known ones.

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

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

notes = spark.table(f"{SCHEMAS.silver}.drift_notes")
distinct_texts = notes.select("note_text").distinct()
print(f"{notes.count()} notes, {distinct_texts.count()} distinct texts, {len(THEMES)} themes")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Status
# MAGIC Not built yet. The cell above profiles the input; the classification is the next piece of work:
# MAGIC 1. Classify the distinct texts into `THEMES` (option A, or option B if AI Functions are unavailable).
# MAGIC 2. Compare with 10 to 15 hand-labeled texts and report agreement before trusting the counts.
# MAGIC 3. Write `gold.note_themes` and `gold.kpi_note_themes`.
# MAGIC 4. Add this notebook to the job before `05_checks`, which already checks that every note is
# MAGIC    tagged exactly once whenever `gold.note_themes` exists.
