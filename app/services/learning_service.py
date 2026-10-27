"""Learn explicit preferences from decision history (spec 10.8). Code enforces the threshold."""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.llm import tasks
from app.llm.client import LLMClient, LLMError
from app.models import ActivityLog, DecisionHistory, LearnedPreference
from app.schemas.llm import PatternCandidate
from app.services.profile_service import get_profile

MIN_EVIDENCE = 3
MIN_CONSISTENCY = 0.8
WINDOW_DAYS = 60
ALLOWED_EFFECTS = {"weight", "standing_approval", "dont_propose", "recommendation"}
PROTECTED_WORDS = ("min_gap", "skip_automatically", "auto_skip", "calendar_write")
PROTECTED_KINDS = {"spacing", "contradiction"}


def summarize(db: Session, today: Date) -> dict[str, Any]:
    """Build a compact summary of recent decisions, grouped by kind, context, and outcome."""
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    groups: dict[str, dict[str, Any]] = {}
    for row in _recent(db, since):
        context = {k: v for k, v in sorted((row.context or {}).items()) if k != "date"}
        key = f"{row.kind}|{sorted(context.items(), key=str)}|{row.signal}|{row.chosen}"
        group = groups.setdefault(
            key,
            {
                "kind": row.kind,
                "context": context,
                "signal": row.signal,
                "chosen": row.chosen,
                "count": 0,
            },
        )
        group["count"] += 1
    return {"window_days": WINDOW_DAYS, "groups": [groups[k] for k in sorted(groups)]}


def _recent(db: Session, since: str) -> list[DecisionHistory]:
    return (
        db.query(DecisionHistory)
        .filter(DecisionHistory.date >= since)
        .order_by(DecisionHistory.id)
        .all()
    )


def _matches(row: DecisionHistory, kind: str, context: dict[str, Any]) -> bool:
    if row.kind != kind:
        return False
    return all((row.context or {}).get(k) == v for k, v in context.items())


def _is_consistent(row: DecisionHistory, signal: str | None, chosen: str | None) -> bool:
    if signal is not None and row.signal != signal:
        return False
    return chosen is None or row.chosen == chosen


def measure(
    db: Session,
    today: Date,
    kind: str,
    context: dict[str, Any],
    signal: str | None,
    chosen: str | None,
) -> tuple[int, float]:
    """Count matching decisions and how many follow the pattern, from real history."""
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    matching = [r for r in _recent(db, since) if _matches(r, kind, context)]
    if not matching:
        return 0, 0.0
    consistent = sum(1 for r in matching if _is_consistent(r, signal, chosen))
    return len(matching), consistent / len(matching)


def _allowed(candidate: PatternCandidate) -> bool:
    """Hard rules cannot be learned away, whatever the LLM returns."""
    effect = candidate.effect
    if effect.get("type") not in ALLOWED_EFFECTS or candidate.kind in PROTECTED_KINDS:
        return False
    text = str(effect).lower()
    return not any(word in text for word in PROTECTED_WORDS)


def _passes(evidence: int, consistency: float) -> bool:
    return evidence >= MIN_EVIDENCE and consistency >= MIN_CONSISTENCY


def analyze(db: Session, today: Date, llm: LLMClient) -> str | None:
    """Re-check existing patterns, then look for new ones. Returns an error message, if any."""
    recheck(db, today)
    summary = summarize(db, today)
    if not summary["groups"]:
        db.commit()
        return None
    try:
        result = tasks.analyze(llm, summary)
    except LLMError as error:
        db.commit()
        return str(error)
    mode = get_profile(db).approval_mode
    for candidate in result.patterns:
        _consider(db, today, candidate, mode)
    db.commit()
    return None


def _consider(db: Session, today: Date, candidate: PatternCandidate, mode: str) -> None:
    if not _allowed(candidate):
        return
    evidence, consistency = measure(
        db, today, candidate.kind, candidate.context,
        candidate.consistent_signal, candidate.consistent_chosen,
    )  # fmt: skip
    if not _passes(evidence, consistency):
        return
    match = {
        "kind": candidate.kind,
        "context": candidate.context,
        "consistent_signal": candidate.consistent_signal,
        "consistent_chosen": candidate.consistent_chosen,
    }
    existing = db.query(LearnedPreference).all()
    if any(row.match == match and row.effect == candidate.effect for row in existing):
        return
    status = "active" if mode == "unrestricted" else "proposed"
    db.add(
        LearnedPreference(
            description=candidate.description,
            match=match,
            effect=candidate.effect,
            evidence_count=evidence,
            consistency=consistency,
            status=status,
        )
    )
    if status == "active":
        _log(db, today, f"Learned: {candidate.description}", "Passed the evidence threshold.")


def recheck(db: Session, today: Date) -> None:
    """Handle drift: a pattern that falls below 80% loses its effect."""
    mode = get_profile(db).approval_mode
    rows = db.query(LearnedPreference).filter(LearnedPreference.status.in_(["active", "proposed"]))
    for row in rows.all():
        match = row.match or {}
        evidence, consistency = measure(
            db, today, match.get("kind", ""), match.get("context", {}),
            match.get("consistent_signal"), match.get("consistent_chosen"),
        )  # fmt: skip
        row.evidence_count = evidence
        row.consistency = consistency
        if row.status != "active" or consistency >= MIN_CONSISTENCY:
            continue
        if mode == "unrestricted":
            row.status = "revoked"
            _log(
                db, today, f"Revoked: {row.description}", f"Consistency fell to {consistency:.0%}."
            )
        else:
            row.status = "proposed"


def _log(db: Session, today: Date, label: str, reason: str) -> None:
    db.add(
        ActivityLog(
            date=today.isoformat(),
            change={"kind": "learned", "label": label},
            reason=reason,
            applied_by="auto",
        )
    )


def set_status(db: Session, pref_id: int, action: str) -> None:
    """Accept or revoke a learned preference."""
    row = db.get(LearnedPreference, pref_id)
    if row is None:
        raise ValueError("That preference no longer exists.")
    row.status = "active" if action == "accept" else "revoked"
    db.commit()
