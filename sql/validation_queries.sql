-- ============================================================================
-- NorthStar Financial — AI Drift Analytics Agent
-- sql/validation_queries.sql
--
-- PURPOSE
--   1. Recreate the drift_full view that the agent and tools.py depend on.
--      The dataset notebook (notebooks/generate_drift_dataset.ipynb) writes
--      ONLY the three base tables. This view is NOT created by that notebook,
--      so a freshly regenerated database is missing it and the agent will fail.
--      Run this file after regenerating data to restore the view.
--   2. Provide post-generation validation/sanity queries used to confirm the
--      dataset is internally consistent before relying on the agent.
--
-- USAGE
--   sqlite3 data/processed/northstar_drift.db < sql/validation_queries.sql
--   -- or paste section 1 into DB Browser for SQLite after each regeneration.
-- ============================================================================


-- ============================================================================
-- SECTION 1 — REQUIRED VIEW (run this every time the database is regenerated)
-- ============================================================================
-- drift_full: pre-joined analytics view of drift_instances + applications.
-- This is the agent's primary query target. The INNER JOIN means applications
-- with no drift instances do not appear here (intentional: this view is the
-- drift surface, not the full portfolio). Use the raw `applications` table when
-- the denominator must include drift-free apps (e.g. drift-percentage metrics).

DROP VIEW IF EXISTS drift_full;

CREATE VIEW drift_full AS
SELECT
    d.drift_id, d.status, d.product_category, d.product,
    d.approved_version, d.detected_version, d.drift_open_date,
    d.drift_close_date, d.drift_duration_days, d.exemption_requested,
    d.exemption_result, d.root_cause, d.environment, d.instance_name,
    d.primary_dc, d.secondary_dc,
    a.app_id, a.app_name, a.app_owner, a.owner_email, a.owner_team,
    a.resiliency_lead, a.lob, a.app_status, a.deploy_status,
    a.in_scope, a.rto_score, a.rto_hours, a.env, a.hosting,
    a.os_family, a.last_test_date, a.last_test_result
FROM drift_instances d
JOIN applications a ON d.app_id = a.app_id;


-- ============================================================================
-- SECTION 2 — ROW COUNT BASELINES
-- ============================================================================
-- Expected against the committed dataset (yours may vary if you regenerate
-- without a fixed seed; the notebook uses random_state=42 for the drift sample):
--   applications     = 1000
--   in_scope = 'Y'   = 750
--   drift_instances  = 475
--   drift_notes      = 804
--   drift_full       = 475   (must equal drift_instances; see Section 3 check)

SELECT 'applications'    AS object, COUNT(*) AS rows FROM applications
UNION ALL SELECT 'in_scope_apps', COUNT(*) FROM applications WHERE in_scope = 'Y'
UNION ALL SELECT 'drift_instances', COUNT(*) FROM drift_instances
UNION ALL SELECT 'drift_notes',     COUNT(*) FROM drift_notes
UNION ALL SELECT 'drift_full',      COUNT(*) FROM drift_full;


-- ============================================================================
-- SECTION 3 — REFERENTIAL INTEGRITY  (every check below must return 0)
-- ============================================================================

-- 3a. The view must not lose or duplicate drift rows vs. its base table.
--     Returns 0 when drift_full and drift_instances have matching row counts.
SELECT (SELECT COUNT(*) FROM drift_instances) - (SELECT COUNT(*) FROM drift_full)
       AS view_row_delta_should_be_0;

-- 3b. No drift instance may reference a non-existent application.
SELECT COUNT(*) AS orphan_drift_should_be_0
FROM drift_instances
WHERE app_id NOT IN (SELECT app_id FROM applications);

-- 3c. No note may reference a non-existent drift instance.
SELECT COUNT(*) AS orphan_notes_should_be_0
FROM drift_notes
WHERE drift_id NOT IN (SELECT drift_id FROM drift_instances);


-- ============================================================================
-- SECTION 4 — DOMAIN LOGIC SANITY  (each must hold, or the data is malformed)
-- ============================================================================

-- 4a. Drift means the detected version differs from the approved version.
--     A drift row where they are equal is a generation bug. Expect 0.
SELECT COUNT(*) AS approved_equals_detected_should_be_0
FROM drift_instances
WHERE approved_version = detected_version;

-- 4b. Status must be exactly 'Open' or 'Closed'. Expect 0 rows.
SELECT status, COUNT(*) AS cnt
FROM drift_instances
WHERE status NOT IN ('Open', 'Closed')
GROUP BY status;

-- 4c. Closed instances should carry a close date; open instances should not.
--     Expect 0 rows from each branch.
SELECT 'closed_without_close_date' AS issue, COUNT(*) AS cnt
FROM drift_instances WHERE status = 'Closed' AND drift_close_date IS NULL
UNION ALL
SELECT 'open_with_close_date', COUNT(*)
FROM drift_instances WHERE status = 'Open' AND drift_close_date IS NOT NULL;

-- 4d. Exemption logic: a result should exist only when one was requested.
--     Expect 0 rows from each branch.
SELECT 'result_without_request' AS issue, COUNT(*) AS cnt
FROM drift_instances WHERE exemption_requested = 'N' AND exemption_result IS NOT NULL
UNION ALL
SELECT 'request_without_result', COUNT(*)
FROM drift_instances WHERE exemption_requested = 'Y' AND exemption_result IS NULL;

-- 4e. rto_hours must agree with rto_score per the RTO_MAP in config.py.
--     Expect 0 rows.
SELECT app_id, rto_score, rto_hours
FROM applications
WHERE rto_hours <> CASE rto_score
    WHEN 1 THEN 0.5  WHEN 2 THEN 1   WHEN 3 THEN 2   WHEN 4 THEN 4
    WHEN 5 THEN 8    WHEN 6 THEN 12  WHEN 7 THEN 24  WHEN 8 THEN 48
    WHEN 9 THEN 72   WHEN 10 THEN 120 END;


-- ============================================================================
-- SECTION 5 — HEADLINE METRIC  (the agent's most-asked KPI)
-- ============================================================================
-- Percentage of in-scope applications with at least one OPEN drift instance.
-- Numerator: distinct in-scope apps that have open drift.
-- Denominator: all in-scope apps (includes drift-free apps — this is why the
-- denominator comes from `applications`, NOT from `drift_full`).
-- 100.0 (not 100) forces float division; integer division would truncate to 0.
-- Expected on the committed dataset: ~17.73

SELECT ROUND(
         COUNT(DISTINCT df.app_id) * 100.0 /
         (SELECT COUNT(DISTINCT app_id) FROM applications WHERE in_scope = 'Y'),
       2) AS open_drift_pct_of_in_scope
FROM drift_full df
WHERE df.status = 'Open'
  AND df.in_scope = 'Y';


-- ============================================================================
-- SECTION 6 — DISTRIBUTION SPOT CHECKS  (eyeball for realism, not pass/fail)
-- ============================================================================

-- 6a. Open drift by product category — expect Operating System / Database heavy.
SELECT product_category, COUNT(*) AS open_drift
FROM drift_full
WHERE status = 'Open'
GROUP BY product_category
ORDER BY open_drift DESC;

-- 6b. Aging buckets against the 60/90/120-day escalation thresholds.
SELECT
  CASE
    WHEN drift_duration_days >= 120 THEN '120+  (executive escalation)'
    WHEN drift_duration_days >=  90 THEN '90-119 (senior escalation)'
    WHEN drift_duration_days >=  60 THEN '60-89  (escalation warning)'
    ELSE                                 '<60    (within tolerance)'
  END AS aging_bucket,
  COUNT(*) AS open_instances
FROM drift_full
WHERE status = 'Open'
GROUP BY aging_bucket
ORDER BY MIN(drift_duration_days) DESC;

-- 6c. Datacenter concentration of open drift (primary site).
SELECT primary_dc, COUNT(*) AS open_drift
FROM drift_full
WHERE status = 'Open'
GROUP BY primary_dc
ORDER BY open_drift DESC;