"""Constrained OpenAI planner for routing analytics questions.

The model is deliberately limited to a small Pydantic schema. It never emits
SQL and cannot widen the application's query capabilities.
"""

from __future__ import annotations

import os

from src.models import ConversationContext, LLMPlannerDecision


ALLOWED_INTENTS = (
    "top_drifting_apps",
    "critical_apps_with_open_drift",
    "drift_by_product",
    "drift_by_data_center",
    "exemption_analysis",
    "aging_bucket_analysis",
    "executive_escalation_candidates",
    "rto_risk_distribution",
)


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

Allowed intents: {", ".join(ALLOWED_INTENTS)}.
Allowed data centers: {", ".join(data_centers)}.
Allowed products: {", ".join(products)}.

Interpret references such as "that", "those", "do that", "narrow it", and "same"
against the prior context. Preserve the prior analysis intent for a scope-only follow-up.
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
