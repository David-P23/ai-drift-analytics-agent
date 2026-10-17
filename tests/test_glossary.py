"""Definitions and out-of-scope questions."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from src import analytics
from src.agent import OUT_OF_SCOPE_ANSWER, answer_question
from src.database import DriftDatabase
from src.glossary import AGING_THRESHOLDS, RTO_TIERS, TERMS, match_definition
from src.models import LLMPlannerDecision

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def demo_db(tmp_path: Path, monkeypatch) -> DriftDatabase:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    copy = tmp_path / "northstar_demo.sqlite"
    shutil.copy(ROOT / "data" / "northstar_demo.sqlite", copy)
    return DriftDatabase(copy, max_rows=1000)


@pytest.mark.parametrize(
    ("question", "key"),
    [
        ("What does mission-critical mean", "mission_critical"),
        ("What does mission-critical mean?", "mission_critical"),
        ("What is drift?", "drift"),
        ("what's an exemption", "exemption"),
        ("What is aging?", "aging"),
        ("What does RTO stand for?", "rto"),
        ("How is the priority score calculated?", "priority_score"),
        ("Define executive escalation", "executive_escalation"),
        ("What does high priority mean?", "high"),
        ("What are aging buckets?", "aging"),
    ],
)
def test_definition_questions_are_recognized(question: str, key: str) -> None:
    term = match_definition(question)
    assert term is not None and term.key == key


@pytest.mark.parametrize(
    "question",
    [
        "Show mission critical apps with open drift.",
        "What is the oldest drift?",
        "Which applications have the oldest open drift?",
        "How is drift concentrated by data center?",
        "Show critical and high priority open drift.",
    ],
)
def test_data_questions_are_not_definitions(question: str) -> None:
    assert match_definition(question) is None


def test_definition_answer_includes_the_live_figure_and_its_query(demo_db: DriftDatabase) -> None:
    response = answer_question(demo_db, "What does mission-critical mean?")
    assert response.intent == "definition"
    assert response.answer.startswith(
        "Mission Critical applications have an RTO score of 1–2, so they must be back up within 30 minutes to 1 hour."
    )
    assert "20 open in-scope drift findings are Mission Critical" in response.answer
    assert response.rows == [{"term": "Mission Critical", "current_value": 20}]
    assert "a.rto_score BETWEEN 1 AND 2" in response.sql
    assert response.suggestions == ["Show mission critical apps with open drift."]


def test_definition_without_a_figure_runs_no_query(demo_db: DriftDatabase) -> None:
    response = answer_question(demo_db, "What does RTO stand for?")
    assert response.intent == "definition" and response.sql is None and not response.rows
    assert "1 = 30 minutes" in response.answer and "10 = 120 hours" in response.answer


def test_scan_date_definition_reports_the_as_of_date(demo_db: DriftDatabase) -> None:
    assert "The latest scan in this data is 2026-04-03." in answer_question(demo_db, "What does scan date mean?").answer


def test_unrelated_question_is_declined_politely(demo_db: DriftDatabase) -> None:
    response = answer_question(demo_db, "What's the weather like this weekend?")
    assert response.intent == "out_of_scope"
    assert response.answer == OUT_OF_SCOPE_ANSWER
    assert response.sql is None and response.suggestions


def test_drift_questions_without_intent_keywords_still_run(demo_db: DriftDatabase) -> None:
    assert answer_question(demo_db, "Show Minneapolis based drift").intent == "top_drifting_apps"


def test_llm_planner_can_route_definitions_and_out_of_scope(demo_db: DriftDatabase, monkeypatch) -> None:
    decisions = {
        "Tell me about exemptions": LLMPlannerDecision(
            question_type="definition", term="exemption", intent="top_drifting_apps"
        ),
        "Who won the game last night?": LLMPlannerDecision(question_type="out_of_scope", intent="top_drifting_apps"),
        "Anything I should worry about?": LLMPlannerDecision(
            question_type="definition", term="not_a_term", intent="executive_escalation_candidates"
        ),
    }
    monkeypatch.setattr("src.prompting.resolve_query_decision", lambda question, **kwargs: decisions[question])
    assert answer_question(demo_db, "Tell me about exemptions").intent == "definition"
    assert answer_question(demo_db, "Who won the game last night?").intent == "out_of_scope"
    # An unknown term falls back to the data intent the planner chose.
    assert answer_question(demo_db, "Anything I should worry about?").intent == "executive_escalation_candidates"


def test_hyphenated_mission_critical_scopes_to_mission_critical_only(monkeypatch) -> None:
    from src.prompting import generate_query_plan

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    plan = generate_query_plan("Show mission-critical apps with open drift")
    assert plan.intent == "critical_apps_with_open_drift" and plan.filters.include_high is False


def test_glossary_rto_tiers_match_the_sql() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE applications (rto_score INTEGER)")
    conn.executemany("INSERT INTO applications VALUES (?)", [(score,) for score in range(1, 11)])
    sql_tiers = dict(conn.execute(f"SELECT a.rto_score, {analytics.RTO_TIER_CASE} FROM applications AS a").fetchall())
    glossary_tiers = {
        score: tier for tier, (low, high) in RTO_TIERS.items() for score in range(low, high + 1)
    }
    assert sql_tiers == glossary_tiers


def test_glossary_aging_thresholds_match_the_sql() -> None:
    case = analytics.AGING_BUCKET_CASE.replace(analytics.AGE_DAYS, "d.days")
    conn = sqlite3.connect(":memory:")
    for days, stage in AGING_THRESHOLDS.items():
        below, at = (conn.execute(f"SELECT {case} FROM (SELECT {n} AS days) AS d").fetchone()[0] for n in (days - 1, days))
        assert below != at and at.startswith(f"{days}"), (days, below, at)
        assert stage.split()[0].lower() in at.lower()


def test_every_term_has_a_name_definition_and_unique_aliases() -> None:
    aliases = [alias for term in TERMS for alias in term.aliases]
    assert len(aliases) == len(set(aliases))
    assert all(term.name and term.definition.endswith(".") for term in TERMS)
