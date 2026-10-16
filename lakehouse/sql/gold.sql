-- Gold layer. Placeholders: ${silver}, ${ref}, ${gold}.
-- Rebuilt in full on every run (small data; a full rebuild is simpler to reason about
-- than incremental gold logic). Deterministic: no LLM touches anything in this file.
-- Aging is point-in-time: days are counted to each record's extract (scan) date,
-- never to the day the pipeline happens to run.

-- 1. One row per drift finding: the drift instance, its application, and its notes.
--    This replaces the v1 drift_full view and the merged SQLite table in v2.
CREATE OR REPLACE TABLE ${gold}.drift_findings
COMMENT 'One row per drift finding across all three source systems, on shared IDs.'
AS
WITH note_rollup AS (
    SELECT
        drift_id,
        COUNT(*) AS note_count,
        MAX(CASE WHEN escalation_flag THEN 1 ELSE 0 END) AS any_escalation
    FROM ${silver}.drift_notes
    GROUP BY drift_id
),
latest_note AS (
    SELECT drift_id, note_date, note_type, author_role, note_text
    FROM (
        SELECT
            n.*,
            -- Same-day notes are common; note_id breaks the tie (it sorts in creation order).
            ROW_NUMBER() OVER (PARTITION BY n.drift_id ORDER BY n.note_date DESC, n.note_id DESC) AS rn
        FROM ${silver}.drift_notes AS n
    ) AS ranked
    WHERE rn = 1
)
SELECT
    CONCAT(d.app_id, '-', d.drift_id) AS finding_id,
    d.app_id,
    d.drift_id,
    a.app_name,
    a.app_owner AS business_owner,
    a.resiliency_lead AS technology_owner,
    d.product,
    a.primary_dc AS data_center,
    d.approved_version,
    d.detected_version,
    a.rto_score,
    d.status,
    d.exemption_requested,
    d.exemption_result,
    a.in_scope,
    d.drift_open_date AS drift_detected_on,
    d.extracted_at AS last_scanned_at,
    CAST(COALESCE(r.note_count, 0) AS INT) AS note_count,
    l.note_date AS latest_note_date,
    l.note_type AS latest_note_type,
    l.author_role AS latest_note_author_role,
    l.note_text AS latest_note_text,
    CASE
        WHEN r.drift_id IS NULL THEN NULL
        WHEN r.any_escalation = 1 THEN 'Y'
        ELSE 'N'
    END AS escalation_flag,
    -- Enrichment beyond the agent's contract
    TO_DATE(d.extracted_at) AS as_of_date,
    d.drift_close_date,
    -- Closed: open -> close. Open: open -> as-of date. Same definition as the v1 dataset.
    CASE
        WHEN d.status = 'Closed' THEN DATEDIFF(d.drift_close_date, d.drift_open_date)
        ELSE DATEDIFF(TO_DATE(d.extracted_at), d.drift_open_date)
    END AS drift_duration_days,
    a.lob,
    d.product_category,
    d.root_cause,
    d.environment,
    d.source_system,
    d.source_drift_id
FROM ${silver}.drift_instances AS d
JOIN ${silver}.applications AS a
    ON a.app_id = d.app_id
LEFT JOIN note_rollup AS r
    ON r.drift_id = d.drift_id
LEFT JOIN latest_note AS l
    ON l.drift_id = d.drift_id;

-- 2. The agent's contract. The analytics agent was built against a table called
--    "applications" with exactly these 23 columns, so a view keeps that contract
--    and the agent's query templates did not have to change.
CREATE OR REPLACE VIEW ${gold}.applications
COMMENT 'Agent-facing view: the 23 columns the drift analytics agent queries.'
AS
SELECT
    finding_id,
    app_id,
    drift_id,
    app_name,
    business_owner,
    technology_owner,
    product,
    data_center,
    approved_version,
    detected_version,
    rto_score,
    status,
    exemption_requested,
    exemption_result,
    in_scope,
    drift_detected_on,
    last_scanned_at,
    note_count,
    latest_note_date,
    latest_note_type,
    latest_note_author_role,
    latest_note_text,
    escalation_flag
FROM ${gold}.drift_findings;

-- 3. Drift penetration by line of business. Denominator is every in-scope
--    application, including the ones with no drift at all.
CREATE OR REPLACE TABLE ${gold}.kpi_drift_by_lob
COMMENT 'Share of in-scope applications with open drift, by line of business.'
AS
WITH per_app AS (
    SELECT
        a.lob,
        a.app_id,
        a.in_scope,
        MAX(CASE WHEN d.status = 'Open' AND d.detected_version <> d.approved_version THEN 1 ELSE 0 END) AS has_open_drift,
        SUM(CASE WHEN d.status = 'Open' AND d.detected_version <> d.approved_version THEN 1 ELSE 0 END) AS open_findings,
        SUM(CASE WHEN d.status = 'Open' AND d.detected_version <> d.approved_version AND a.rto_score <= 2 THEN 1 ELSE 0 END)
            AS mission_critical_open
    FROM ${silver}.applications AS a
    LEFT JOIN ${silver}.drift_instances AS d
        ON d.app_id = a.app_id
    GROUP BY a.lob, a.app_id, a.in_scope
)
SELECT
    lob,
    COUNT(*) AS in_scope_apps,
    SUM(has_open_drift) AS apps_with_open_drift,
    ROUND(100.0 * SUM(has_open_drift) / COUNT(*), 1) AS open_drift_pct,
    SUM(open_findings) AS open_findings,
    SUM(mission_critical_open) AS mission_critical_open
FROM per_app
WHERE in_scope = 'Y'
GROUP BY lob;

-- 4. Aging buckets for open in-scope drift (60 / 90 / 120-day thresholds),
--    as of the extract date.
CREATE OR REPLACE TABLE ${gold}.kpi_aging_buckets
COMMENT 'Open in-scope drift by aging threshold, as of the extract (scan) date.'
AS
WITH open_drift AS (
    SELECT
        f.*,
        f.drift_duration_days AS days_open
    FROM ${gold}.drift_findings AS f
    WHERE f.status = 'Open'
      AND f.in_scope = 'Y'
      AND f.detected_version <> f.approved_version
)
SELECT
    MAX(as_of_date) AS as_of_date,
    CASE
        WHEN days_open >= 120 THEN '120+ Executive escalation'
        WHEN days_open >= 90 THEN '90-119 Senior escalation'
        WHEN days_open >= 60 THEN '60-89 Warning'
        ELSE '<60 Current'
    END AS aging_bucket,
    COUNT(*) AS open_findings,
    SUM(CASE WHEN rto_score <= 4 THEN 1 ELSE 0 END) AS critical_high_findings,
    MAX(days_open) AS oldest_days_open
FROM open_drift
GROUP BY 2;

-- 5. The same KPIs split by source system: how each acquired company compares
--    once everything is on shared definitions.
CREATE OR REPLACE TABLE ${gold}.kpi_by_source_system
COMMENT 'Drift posture by originating source system (acquired company).'
AS
SELECT
    f.source_system,
    COUNT(*) AS findings,
    SUM(CASE WHEN f.status = 'Open' AND f.in_scope = 'Y' THEN 1 ELSE 0 END) AS open_in_scope_findings,
    SUM(CASE WHEN f.status = 'Open' AND f.in_scope = 'Y' AND f.rto_score <= 2 THEN 1 ELSE 0 END) AS mission_critical_open,
    SUM(CASE WHEN f.exemption_result = 'Pending' THEN 1 ELSE 0 END) AS pending_exemptions,
    SUM(CASE WHEN f.escalation_flag = 'Y' THEN 1 ELSE 0 END) AS escalated_findings,
    ROUND(AVG(CASE WHEN f.status = 'Open' THEN f.drift_duration_days END), 1) AS avg_days_open,
    MAX(f.as_of_date) AS as_of_date
FROM ${gold}.drift_findings AS f
GROUP BY f.source_system;
