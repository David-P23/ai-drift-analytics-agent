"""Small, inspectable retrieval-augmented policy guidance for the demo."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import re

from src.models import PolicySource


POLICY_DIRECTORY = Path(__file__).resolve().parents[1] / "docs" / "policies"
POLICY_KEYWORDS = (
    "policy",
    "governance",
    "control",
    "regulatory",
    "regulation",
    "standard",
    "initiative",
    "risk acceptance",
    "exception process",
    "exception approval",
)
DATA_KEYWORDS = (
    "drift",
    "application",
    "app",
    "product",
    "data center",
    "datacenter",
    "rto",
    "mission critical",
    "high",
    "aging",
    "exemption",
    "escalation",
)


@dataclass(frozen=True)
class PolicyChunk:
    document: str
    section: str
    content: str

    @property
    def searchable_text(self) -> str:
        return f"{self.document}\n{self.section}\n{self.content}"

    def as_source(self) -> PolicySource:
        excerpt = " ".join(self.content.split())
        return PolicySource(document=self.document, section=self.section, excerpt=excerpt[:360])


@dataclass(frozen=True)
class PolicyRetrieval:
    sources: tuple[PolicySource, ...]
    mode: str
    context: str


def should_retrieve_policy(question: str) -> bool:
    normalized = question.casefold()
    return any(keyword in normalized for keyword in POLICY_KEYWORDS)


def is_policy_only_question(question: str) -> bool:
    normalized = question.casefold()
    return should_retrieve_policy(question) and not any(keyword in normalized for keyword in DATA_KEYWORDS)


def retrieve_policy_context(question: str, *, top_k: int = 3) -> PolicyRetrieval | None:
    """Retrieve relevant demo-policy chunks using embeddings with lexical fallback."""

    chunks = _load_policy_chunks()
    if not chunks:
        return None

    ranked = _semantic_rank(question, chunks)
    mode = "semantic"
    if not ranked:
        ranked = _lexical_rank(question, chunks)
        mode = "lexical"

    selected = ranked[:top_k]
    if not selected:
        return None
    sources = tuple(chunk.as_source() for chunk in selected)
    context = "\n\n".join(
        f"[{chunk.document} > {chunk.section}]\n{chunk.content}" for chunk in selected
    )
    return PolicyRetrieval(sources=sources, mode=mode, context=context)


def compose_policy_guidance(
    question: str,
    retrieval: PolicyRetrieval,
    *,
    data_answer: str | None = None,
) -> str:
    """Synthesize a policy answer from retrieved text, with a deterministic fallback."""

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            from openai import OpenAI

            evidence = retrieval.context
            if data_answer:
                evidence = f"Analytics result:\n{data_answer}\n\nPolicy evidence:\n{evidence}"
            completion = OpenAI(api_key=api_key, timeout=15.0, max_retries=1).chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Answer using only the supplied synthetic NorthStar policy evidence and any supplied "
                            "analytics result. Do not claim legal advice or live regulatory status. Be concise, "
                            "actionable, and cite the document title and section in square brackets."
                        ),
                    },
                    {"role": "user", "content": f"Question: {question}\n\n{evidence}"},
                ],
            )
            answer = completion.choices[0].message.content
            if answer:
                return answer.strip()
        except Exception:
            pass

    lead = retrieval.sources[0]
    guidance = lead.excerpt
    if data_answer:
        return f"{data_answer} Policy context: {guidance} [{lead.document} > {lead.section}]"
    return f"Based on the demo policy corpus: {guidance} [{lead.document} > {lead.section}]"


@lru_cache(maxsize=1)
def _load_policy_chunks() -> tuple[PolicyChunk, ...]:
    chunks: list[PolicyChunk] = []
    for path in sorted(POLICY_DIRECTORY.glob("*.md")):
        title = path.stem.replace("-", " ").title()
        section = "Overview"
        content: list[str] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                continue
            if line.startswith("## "):
                if content:
                    chunks.append(PolicyChunk(title, section, "\n".join(content).strip()))
                section = line[3:].strip()
                content = []
                continue
            if line:
                content.append(line)
        if content:
            chunks.append(PolicyChunk(title, section, "\n".join(content).strip()))
    return tuple(chunk for chunk in chunks if chunk.content)


def _semantic_rank(question: str, chunks: tuple[PolicyChunk, ...]) -> list[PolicyChunk]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    try:
        from openai import OpenAI

        model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        document_vectors = _document_embeddings(model, tuple(chunk.searchable_text for chunk in chunks))
        query_response = OpenAI(api_key=api_key, timeout=10.0, max_retries=1).embeddings.create(
            model=model,
            input=question,
        )
        query_vector = query_response.data[0].embedding
        ranked = sorted(
            zip(chunks, document_vectors, strict=True),
            key=lambda item: _cosine_similarity(query_vector, item[1]),
            reverse=True,
        )
        return [chunk for chunk, _ in ranked]
    except Exception:
        return []


@lru_cache(maxsize=4)
def _document_embeddings(model: str, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    from openai import OpenAI

    response = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=15.0, max_retries=1).embeddings.create(
        model=model,
        input=list(texts),
    )
    return tuple(tuple(item.embedding) for item in response.data)


def _lexical_rank(question: str, chunks: tuple[PolicyChunk, ...]) -> list[PolicyChunk]:
    tokens = set(_tokens(question))
    return sorted(
        chunks,
        key=lambda chunk: (len(tokens.intersection(_tokens(chunk.searchable_text))), chunk.document, chunk.section),
        reverse=True,
    )


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2]


def _cosine_similarity(left: list[float], right: tuple[float, ...]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    if not denominator:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator
