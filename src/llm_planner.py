"""Constrained OpenAI planner for routing analytics questions.

The model is deliberately limited to a small Pydantic schema. It never emits
SQL and cannot widen the application's query capabilities.
"""

from __future__ import annotations

import os

from src.glossary import TERM_KEYS
from src.models import ConversationContext, LLMPlannerDecision


INTENT_GUIDE = {
    "top_drifting_apps": "list of individual open findings, oldest first (which applications, oldest, longest open)",
    "critical_apps_with_open_drift": "list of Mission Critical findings, plus High when asked",
    "drift_by_product": "open finding counts by product",
    "drift_by_data_center": "open finding counts by data center",
    "exemption_analysis": "open finding counts by exemption request and decision",
    "aging_bucket_analysis": "open finding counts in the <60, 60-89, 90-119 and 120+ day buckets",
    "executive_escalation_candidates": "list of findings open 120 days or more",
    "rto_risk_distribution": "open finding counts by RTO tier",
}
ALLOWED_INTENTS = tuple(INTENT_GUIDE)


def resolve_query_decision(
    question: str,
    *,
    context: ConversationContext | None,
    data_centers: list[str],
    products: list[str],
) -> LLMPlannerDecision | None:
    """Return a typed routing decision, or ``None`` to use deterministic routing."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=12.0, max_retries=1)
        completion = client.chat.completions.parse(
            model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            messages=[
                {"role": "system", "content": _planner_instructions(data_centers, products)},
                {"role": "user", "content": _planner_request(question, context)},
            ],
            response_format=LLMPlannerDecision,
        )
        return completion.choices[0].message.parsed
    except Exception:
        # Keep the public demo usable during a provider outage, quota issue, or bad key.
        return None


def _planner_instructions(data_centers: list[str], products: list[str]) -> str:
    return f"""
You route enterprise application-drift analytics questions into a constrained plan.
Return only the supplied schema. Never write SQL, explain reasoning, invent filters,
or answer the business question.

Question types:
- data: the question asks about the drift data (the default).
- definition: the question asks what a term means or how it is calculated. Set term to one of:
  {", ".join(TERM_KEYS)}.
- out_of_scope: the question is not about application drift in this portfolio.
Always return an intent; it is used only when the question type is data.

Allowed intents:
{chr(10).join(f"- {name}: {meaning}" for name, meaning in INTENT_GUIDE.items())}
Allowed data centers: {", ".join(data_centers)}.
Allowed products: {", ".join(products)}.

Interpret references such as "that", "those", "do that", "narrow it", and "same"
against the prior context. Preserve the prior analysis intent only for a scope-only
follow-up ("do that for Phoenix", "only Kafka"). A question that asks for a different
analysis is a new request: choose its intent from the question itself.
Use null for a filter that the request does not specify or inherit. Set include_high true
only when the user asks for High/priority findings alongside Mission Critical; set false
for Mission Critical-only questions; otherwise preserve or return null.
""".strip()


def _planner_request(question: str, context: ConversationContext | None) -> str:
    if not context:
        return f"Question: {question}"

    return (
        f"Prior resolved request: {context.resolved_question}\n"
        f"Prior intent: {context.intent}\n"
        f"Prior filters: {context.filters.model_dump_json()}\n"
        f"Question: {question}"
    )
