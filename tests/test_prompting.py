from __future__ import annotations

from src.prompting import build_text_to_sql_prompt, generate_query_plan


def test_prompt_contains_domain_rules_and_schema() -> None:
    prompt = build_text_to_sql_prompt("Show critical drift")
    assert "Drift means detected_version differs from approved_version." in prompt.system
    assert "rto_score 1-2 means Mission Critical." in prompt.system
    assert "Aging threshold 120 days means executive escalation." in prompt.system
    assert "exemption_requested values are Y and N." in prompt.system
    assert "applications.approved_version" in prompt.system
    assert prompt.user == "User question: Show critical drift"


def test_critical_high_priority_plan_uses_explicit_rto_logic() -> None:
    plan = generate_query_plan("Show critical and high priority open drift")
    assert "a.rto_score BETWEEN 1 AND 4" in plan.sql
    assert "a.status = 'Open'" in plan.sql
    assert "a.in_scope = 'Y'" in plan.sql
    assert "a.detected_version <> a.approved_version" in plan.sql
    assert "applications AS a" in plan.sql


def test_mission_critical_plan_uses_rto_one_to_two() -> None:
    plan = generate_query_plan("Show mission critical apps with open drift")
    assert "a.rto_score BETWEEN 1 AND 2" in plan.sql
    assert plan.intent == "critical_apps_with_open_drift"


def test_product_question_routes_to_product_chart() -> None:
    plan = generate_query_plan("Where is drift concentrated by product?")
    assert plan.intent == "drift_by_product"
    assert plan.chart is not None
    assert plan.chart.x == "product"
    assert plan.chart.y == "drift_count"


def test_critical_high_question_can_scope_to_data_center() -> None:
    plan = generate_query_plan("Show critical and high priority open drift relating to the Minneapolis Datacenter")
    assert plan.intent == "critical_apps_with_open_drift"
    assert "a.rto_score BETWEEN 1 AND 4" in plan.sql
    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in plan.sql


def test_rto_distribution_can_scope_to_data_center() -> None:
    plan = generate_query_plan("Show RTO risk distribution for drift in the Minneapolis Datacenter")
    assert plan.intent == "rto_risk_distribution"
    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in plan.sql
    assert "GROUP BY rto_tier" in plan.sql
