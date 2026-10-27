"""Task wrappers around the LLM. Outputs are only drafts, questions, or proposals."""

from __future__ import annotations

from typing import Any

from app.llm.client import LLMClient, call_structured
from app.schemas.llm import (
    ClassificationResult,
    ContradictionsResult,
    ExplanationResult,
    MedicineSuggestion,
    ParseResult,
    PatternsResult,
    PrepResult,
    QuestionsResult,
)


def parse(client: LLMClient, text: str, rules: list[dict[str, Any]]) -> ParseResult:
    """Turn messy text into draft rules."""
    return call_structured(client, "parse", ParseResult, {"text": text}, {"confirmed_rules": rules})


def questions(
    client: LLMClient, text: str, drafts: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> QuestionsResult:
    """Find gaps in the drafts and rules that need a follow-up question."""
    context = {"drafts": drafts, "confirmed_rules": rules}
    return call_structured(client, "questions", QuestionsResult, {"text": text}, context)


def contradictions(
    client: LLMClient, new_rule: dict[str, Any], existing: list[dict[str, Any]]
) -> ContradictionsResult:
    """Compare a rule with confirmed rules and return direct contradictions."""
    primary = {"new_rule": new_rule, "existing": existing}
    return call_structured(client, "contradictions", ContradictionsResult, primary)


def prep(client: LLMClient, events: list[dict[str, Any]], context: dict[str, Any]) -> PrepResult:
    """Propose prep tasks nobody stated."""
    return call_structured(client, "prep", PrepResult, {"events": events}, context)


def medicine(client: LLMClient, label: str, rules: list[dict[str, Any]]) -> MedicineSuggestion:
    """Suggest timing for a medicine that has none."""
    context = {"confirmed_rules": rules}
    return call_structured(client, "medicine", MedicineSuggestion, {"label": label}, context)


def classify(client: LLMClient, title: str) -> ClassificationResult:
    """Classify a calendar title that keywords could not."""
    return call_structured(client, "classify", ClassificationResult, {"title": title})


def explain(client: LLMClient, decision: dict[str, Any]) -> ExplanationResult:
    """Turn a scheduler decision into one line. Never changes the decision."""
    return call_structured(client, "explain", ExplanationResult, decision)


def analyze(client: LLMClient, summary: dict[str, Any]) -> PatternsResult:
    """Find candidate patterns in a summary of decision history."""
    return call_structured(client, "analyze", PatternsResult, summary)
