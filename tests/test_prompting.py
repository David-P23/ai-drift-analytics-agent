from __future__ import annotations

from src.models import ConversationContext, LLMPlannerDecision, PolicyGuidance
from src.prompting import build_text_to_sql_prompt, generate_query_plan
from src import analytics
from src.policy_rag import _format_policy_guidance, is_policy_only_question, retrieve_policy_context, should_retrieve_policy


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


def test_follow_up_inherits_mission_critical_intent_and_applies_new_data_center() -> None:
    first_plan = generate_query_plan("Show mission critical apps with open drift")
    context = ConversationContext(
        intent=first_plan.intent,
        resolved_question=first_plan.resolved_question or "",
        filters=first_plan.filters,
    )

    follow_up = generate_query_plan("Let's narrow that down to Minneapolis-based drift", context=context)

    assert follow_up.intent == "critical_apps_with_open_drift"
    assert follow_up.filters.data_center == "Minneapolis"
    assert follow_up.filters.include_high is False
    assert "a.rto_score BETWEEN 1 AND 2" in follow_up.sql
    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in follow_up.sql


def test_follow_up_treats_datacenter_as_scope_not_a_new_concentration_report() -> None:
    first_plan = generate_query_plan("Show mission critical apps with open drift")
    context = ConversationContext(
        intent=first_plan.intent,
        resolved_question=first_plan.resolved_question or "",
        filters=first_plan.filters,
    )

    follow_up = generate_query_plan("Let's do that for the Minneapolis Datacenter now", context=context)

    assert follow_up.intent == "critical_apps_with_open_drift"
    assert follow_up.filters.data_center == "Minneapolis"
    assert "a.rto_score BETWEEN 1 AND 2" in follow_up.sql


def test_data_center_only_question_scopes_oldest_drift_query() -> None:
    plan = generate_query_plan("Show Minneapolis based drift")

    assert plan.intent == "top_drifting_apps"
    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in plan.sql


def test_exemption_follow_up_scopes_the_same_analysis_to_a_data_center() -> None:
    first_plan = generate_query_plan("Analyze exemption status for open drift")
    context = ConversationContext(
        intent=first_plan.intent,
        resolved_question=first_plan.resolved_question or "",
        filters=first_plan.filters,
    )

    follow_up = generate_query_plan("Now do that for drift out of the Ashburn datacenter", context=context)

    assert follow_up.intent == "exemption_analysis"
    assert follow_up.filters.data_center == "Ashburn"
    assert "LOWER(a.data_center) = LOWER('Ashburn')" in follow_up.sql


def test_aging_and_escalation_queries_accept_data_center_scope() -> None:
    aging = generate_query_plan("Show aging buckets for Minneapolis")
    escalation = generate_query_plan("Show executive escalation in Minneapolis")

    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in aging.sql
    assert "LOWER(a.data_center) = LOWER('Minneapolis')" in escalation.sql


def test_llm_planner_can_select_a_scoped_exemption_analysis(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.prompting.resolve_query_decision",
        lambda *args, **kwargs: LLMPlannerDecision(
            intent="exemption_analysis",
            data_center="Ashburn",
        ),
    )

    plan = generate_query_plan("Can you look at exemptions in our Ashburn facility?")

    assert plan.planner == "llm"
    assert plan.intent == "exemption_analysis"
    assert plan.filters.data_center == "Ashburn"
    assert "LOWER(a.data_center) = LOWER('Ashburn')" in plan.sql


def test_llm_planner_rejects_unapproved_scope_values(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.prompting.resolve_query_decision",
        lambda *args, **kwargs: LLMPlannerDecision(
            intent="critical_apps_with_open_drift",
            data_center="Imaginary Site",
        ),
    )

    plan = generate_query_plan("Show Mission Critical drift in Minneapolis")

    assert plan.planner == "llm"
    assert plan.filters.data_center == "Minneapolis"


def test_dashboard_only_plans_remain_valid_query_plans() -> None:
    assert analytics.ai_risk_intelligence().intent == "ai_risk_intelligence"
    assert analytics.drift_cluster_source().intent == "drift_cluster_source"


def test_policy_retrieval_has_lexical_fallback_without_an_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    retrieval = retrieve_policy_context("What is the exception approval policy for Mission Critical systems?")

    assert retrieval is not None
    assert retrieval.mode == "lexical"
    assert retrieval.sources
    assert "Exception" in retrieval.sources[0].document


def test_policy_detection_distinguishes_policy_only_and_hybrid_questions() -> None:
    assert should_retrieve_policy("What does the risk acceptance policy require?")
    assert is_policy_only_question("What does the risk acceptance policy require?")
    assert not is_policy_only_question("Which drift findings have a pending exception under policy?")


def test_policy_guidance_format_is_brief_and_does_not_repeat_analytics() -> None:
    rendered = _format_policy_guidance(
        PolicyGuidance(
            summary="Pending exemptions require monthly review.",
            recommended_actions=["Schedule the governance review.", "Confirm the accountable owner."],
        ),
        data_answer="One Ashburn exemption is pending review.",
    )

    assert rendered.count("One Ashburn exemption is pending review.") == 1
    assert rendered.count("Recommended next actions:") == 1
    assert rendered.count("- ") == 2
