import sqlite3
import pandas as pd
from src.config import DB_PATH

def run_query(sql: str) -> str:
    """Execute a SQL query against the drift database and return results as a string."""
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(sql, conn)
        conn.close()
        if df.empty:
            return "No results found."
        return df.to_string(index=False)
    except Exception as e:
        return f"Query error: {str(e)}"

def get_drift_percentage() -> str:
    """Calculate what percentage of in-scope applications are currently drifting."""
    sql = """
        SELECT ROUND(
            COUNT(DISTINCT df.app_id) * 100.0 /
            (SELECT COUNT(DISTINCT app_id) FROM applications WHERE in_scope = 'Y'),
        2) AS drift_percentage
        FROM drift_full df
        WHERE df.status = 'Open'
    """
    return run_query(sql)

def get_schema() -> str:
    """Return the database schema for all three tables."""
    return """
    PREFERRED VIEW (use this for most queries):

    drift_full — pre-joined view of drift_instances + applications
    Contains all drift fields plus: app_name, app_owner, owner_email, owner_team,
    resiliency_lead, lob, app_status, deploy_status, in_scope, rto_score, rto_hours,
    env, hosting, os_family, last_test_date, last_test_result

    Use drift_full as the primary table for any question involving both drift and application data.
    Only use raw tables (applications, drift_instances, drift_notes) when drift_full doesn't have what you need.

    TABLES:

    applications(app_id, app_name, app_owner, owner_email, owner_team, resiliency_lead,
                lob, app_status, deploy_status, in_scope, rto_score, rto_hours,
                primary_dc, secondary_dc, env, hosting, os_family, last_test_date, last_test_result)

    drift_instances(drift_id, app_id, lob, rto_score, rto_hours, resiliency_lead,
                    primary_dc, secondary_dc, product_category, product, instance_name,
                    approved_version, detected_version, status, drift_open_date,
                    drift_close_date, drift_duration_days, exemption_requested,
                    exemption_result, root_cause, environment)

    drift_notes(note_id, drift_id, app_id, note_date, note_type, author_role,
                note_text, escalation_flag)

    drift_full — VIEW (drift_instances JOIN applications):
    (drift_id, status, product_category, product, approved_version, detected_version,
    drift_open_date, drift_close_date, drift_duration_days, exemption_requested,
    exemption_result, root_cause, environment, instance_name, primary_dc, secondary_dc,
    app_id, app_name, app_owner, owner_email, owner_team, resiliency_lead, lob,
    app_status, deploy_status, in_scope, rto_score, rto_hours, env, hosting,
    os_family, last_test_date, last_test_result)
    """