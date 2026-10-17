"""Business definitions the agent can explain, kept in one place.

Definitions use the same rules as the SQL templates in src/analytics.py (tests
check that the RTO tiers and aging thresholds below match the templates), and a
term can carry a live figure computed by a validated, read-only query, so a
definition always comes with what it means in the current data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.analytics import AGE_DAYS, OPEN_SCOPE_DRIFT

# RTO score -> maximum hours to recover (the NorthStar recovery scale).
RTO_HOURS: dict[int, float] = {1: 0.5, 2: 1, 3: 2, 4: 4, 5: 8, 6: 12, 7: 24, 8: 48, 9: 72, 10: 120}
# Tier -> (lowest score, highest score). Same ranges as RTO_TIER_CASE.
RTO_TIERS: dict[str, tuple[int, int]] = {
    "Mission Critical": (1, 2),
    "High": (3, 4),
    "Medium": (5, 6),
    "Low": (7, 10),
}
# Days open -> stage. Same thresholds as AGING_BUCKET_CASE.
AGING_THRESHOLDS: dict[int, str] = {60: "warning", 90: "senior escalation", 120: "executive escalation"}


def _hours(score: int) -> str:
    hours = RTO_HOURS[score]
    return "30 minutes" if hours == 0.5 else f"{hours:g} hour" + ("" if hours == 1 else "s")


def _tier_window(tier: str) -> str:
    low, high = RTO_TIERS[tier]
    return f"RTO score of {low}–{high}, so they must be back up within {_hours(low)} to {_hours(high)}"


def _count_where(condition: str) -> str:
    return f"SELECT COUNT(*) AS figure FROM applications AS a WHERE {OPEN_SCOPE_DRIFT} AND {condition}"


@dataclass(frozen=True)
class Term:
    key: str
    name: str
    aliases: tuple[str, ...]
    definition: str
    figure_sql: str | None = None
    figure_text: str = ""  # "{figure}" is replaced by the query result
    try_next: str | None = None


_SCALE = ", ".join(f"{score} = {_hours(score)}" for score in RTO_HOURS)

TERMS: tuple[Term, ...] = (
    Term(
        "drift",
        "Drift",
        ("drift", "application drift", "version drift", "drifting"),
        "Drift is when the software version detected on an application differs from the version the company "
        "approved. Open drift is unremediated risk: an unapproved version may be unsupported, unpatched, or "
        "fail during a recovery.",
        f"SELECT COUNT(*) AS figure FROM applications AS a WHERE {OPEN_SCOPE_DRIFT}",
        "As of the latest scan, {figure} in-scope drift findings are open.",
        "Which applications have the oldest open drift?",
    ),
    Term(
        "finding",
        "Finding",
        ("finding", "findings", "drift finding"),
        "A finding is one drift record: one application, one product, and the gap between its approved and "
        "detected versions. One application can have several findings, so counts in this agent are findings, "
        "not applications.",
        "SELECT COUNT(*) AS figure FROM applications AS a",
        "The portfolio holds {figure} findings, open and closed.",
        "Which applications have the oldest open drift?",
    ),
    Term(
        "rto",
        "RTO (Recovery Time Objective)",
        ("rto", "rto score", "recovery time objective", "recovery time"),
        "RTO, the Recovery Time Objective, is the longest an application can be down before the business is "
        f"harmed. It is scored from 1 to 10: {_SCALE}. Lower scores are more critical.",
        None,
        "",
        "Show RTO risk distribution.",
    ),
    Term(
        "mission_critical",
        "Mission Critical",
        ("mission critical",),
        f"Mission Critical applications have an {_tier_window('Mission Critical')}. Drift on them is the "
        "highest priority.",
        _count_where("a.rto_score BETWEEN 1 AND 2"),
        "As of the latest scan, {figure} open in-scope drift findings are Mission Critical.",
        "Show mission critical apps with open drift.",
    ),
    Term(
        "high",
        "High",
        ("high tier", "high priority", "high"),
        f"High-tier applications have an {_tier_window('High')}. Questions about critical and high priority "
        "drift include both Mission Critical and High.",
        _count_where("a.rto_score BETWEEN 3 AND 4"),
        "As of the latest scan, {figure} open in-scope drift findings are High.",
        "Show critical and high priority open drift.",
    ),
    Term(
        "medium",
        "Medium",
        ("medium tier", "medium"),
        f"Medium-tier applications have an {_tier_window('Medium')}.",
        _count_where("a.rto_score BETWEEN 5 AND 6"),
        "As of the latest scan, {figure} open in-scope drift findings are Medium.",
        "Show RTO risk distribution.",
    ),
    Term(
        "low",
        "Low",
        ("low tier", "low"),
        f"Low-tier applications have an {_tier_window('Low')}.",
        _count_where("a.rto_score BETWEEN 7 AND 10"),
        "As of the latest scan, {figure} open in-scope drift findings are Low.",
        "Show RTO risk distribution.",
    ),
    Term(
        "in_scope",
        "In scope",
        ("in scope", "compliance scope", "scope"),
        "An in-scope application is subject to drift compliance monitoring. The KPI cards and answers count "
        "in-scope findings only.",
        "SELECT COUNT(*) AS figure FROM applications AS a WHERE a.in_scope = 'Y'",
        "{figure} findings belong to in-scope applications.",
        None,
    ),
    Term(
        "exemption",
        "Exemption",
        ("exemption", "exemptions", "exemption request", "exception"),
        "An exemption is a request to stay on the unapproved version for a set time, usually because of a "
        "compatibility issue or a planned retirement. The result is Approved, Pending or Denied. Pending and "
        "Denied exemptions raise a finding's priority; an Approved one lowers it.",
        _count_where("a.exemption_result = 'Pending'"),
        "As of the latest scan, {figure} open in-scope drift findings have a pending exemption.",
        "Analyze exemption status for open drift.",
    ),
    Term(
        "aging",
        "Aging and days open",
        ("aging", "aging bucket", "aging buckets", "aging threshold", "aging thresholds", "days open", "age"),
        "Days open are counted from the date drift was detected to the latest scan date, so the figures describe "
        "the portfolio as of that scan and do not change as the calendar moves. Thresholds: "
        + ", ".join(f"{days} days = {stage}" for days, stage in AGING_THRESHOLDS.items())
        + ".",
        _count_where(f"{AGE_DAYS} >= 90"),
        "As of the latest scan, {figure} open in-scope drift findings are at senior or executive escalation age.",
        "Show aging buckets for open drift.",
    ),
    Term(
        "executive_escalation",
        "Executive escalation",
        ("executive escalation", "executive escalations"),
        "A finding reaches executive escalation when it has been open 120 days or more as of the latest scan. "
        "It goes to executive leadership for a decision.",
        _count_where(f"{AGE_DAYS} >= 120"),
        "As of the latest scan, {figure} open in-scope drift findings are at executive escalation.",
        "Which apps need executive escalation?",
    ),
    Term(
        "senior_escalation",
        "Senior escalation",
        ("senior escalation",),
        "A finding reaches senior escalation at 90 to 119 days open as of the latest scan. At 120 days it moves "
        "to executive escalation.",
        _count_where(f"{AGE_DAYS} BETWEEN 90 AND 119"),
        "As of the latest scan, {figure} open in-scope drift findings are at senior escalation.",
        "Show aging buckets for open drift.",
    ),
    Term(
        "priority_score",
        "Priority score",
        ("priority score", "ai priority score", "risk score", "priority"),
        "The priority score ranks open drift with fixed points, not a model: RTO tier (Mission Critical 40, "
        "High 30, Medium 18, Low 8) plus age (120+ days 35, 90+ days 25, 60+ days 15, otherwise 5) plus "
        "exemption (Denied +18, Pending +15, Approved −8). 85 and above is Executive intervention, 70 and "
        "above Remediation command, 50 and above Governance watch, and the rest Managed backlog.",
        None,
        "",
        None,
    ),
    Term(
        "cluster",
        "Drift cluster",
        ("cluster", "clusters", "drift cluster", "drift wave", "wave"),
        "A drift cluster is a wave of findings on the same product and approved version detected close "
        "together: by default 3 or more within a 30-day window. A cluster usually means one patch or rollout "
        "missed many applications at once. The Cluster Detection Lab lets you change the threshold, window "
        "and grouping.",
        None,
        "",
        None,
    ),
    Term(
        "scan_date",
        "Scan date",
        ("scan date", "last scanned", "as of", "as of date", "latest scan"),
        "The scan date is when the inventory scan last checked each application's versions. Aging is measured "
        "to it, so every figure is as of that scan.",
        "SELECT MAX(a.last_scanned_at) AS figure FROM applications AS a",
        "The latest scan in this data is {figure}.",
        None,
    ),
)

_BY_ALIAS: list[tuple[str, Term]] = sorted(
    ((alias, term) for term in TERMS for alias in term.aliases), key=lambda pair: len(pair[0]), reverse=True
)
TERM_KEYS: tuple[str, ...] = tuple(term.key for term in TERMS)
_BY_KEY = {term.key: term for term in TERMS}

_DEFINITION_CUE = re.compile(
    r"\bmean(s|ing)?\b|\bdefin(e|ed|ition)\b|\bstands? for\b"
    r"|\bhow (is|are) .+ (calculated|measured|defined|scored|determined)\b"
)
_WHAT_IS = re.compile(r"^(?:what s|whats|what) (?:(?:is|are) )?(?:(?:a|an|the) )?(?P<rest>.+)$")


def normalize(question: str) -> str:
    text = question.casefold().replace("-", " ").replace("’", "'")
    text = re.sub(r"[^\w\s']", " ", text).replace("'", " ")
    return " ".join(text.split())


def term_by_key(key: str | None) -> Term | None:
    return _BY_KEY.get(key or "")


def find_term(question: str) -> Term | None:
    """The longest glossary alias mentioned in the question, if any."""

    text = f" {normalize(question)} "
    for alias, term in _BY_ALIAS:
        if f" {alias} " in text:
            return term
    return None


def match_definition(question: str) -> Term | None:
    """Return the term when the question asks what it means; None for data questions.

    "What does mission-critical mean?" and "What is an exemption?" are definitions.
    "Show mission critical apps" and "What is the oldest drift?" are not.
    """

    text = normalize(question)
    term = find_term(text)
    if not term:
        return None
    if _DEFINITION_CUE.search(text):
        return term
    what_is = _WHAT_IS.match(text)
    if what_is and any(what_is.group("rest") == alias for alias in term.aliases):
        return term
    return None
