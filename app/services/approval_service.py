"""Answer approvals, record every decision, and undo auto-applied changes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    ActivityLog,
    Approval,
    DecisionHistory,
    Rule,
    StandingApprovalRow,
    TitleClassification,
)
from app.services import rule_service


def record_decision(
    db: Session,
    iso: str,
    kind: str,
    context: dict[str, Any],
    options: list[Any],
    chosen: Any,
    signal: str,
) -> None:
    """Add one row to decision_history. Every user decision goes through here."""
    db.add(
        DecisionHistory(
            date=iso,
            kind=kind,
            context=context or {},
            options_offered=options,
            chosen=chosen,
            signal=signal,
        )
    )


def _option_ids(row: Approval) -> list[str]:
    top = (row.proposed_change or {}).get("id")
    others = [a.get("id") for a in row.alternatives or []]
    return [o for o in [top, *others] if o]


def decide(db: Session, approval_id: int, action: str, option_id: str | None = None) -> Approval:
    """Apply approve, reject, always, or pick to an approval."""
    row = db.get(Approval, approval_id)
    if row is None:
        raise ValueError("That approval no longer exists.")
    top = (row.proposed_change or {}).get("id")
    if action == "pick":
        chosen = option_id
        signal = "approved" if option_id == top else "picked_alternative"
        row.status = "approved"
    elif action == "reject":
        chosen, signal = None, "rejected"
        row.status = "rejected"
    else:
        chosen = top
        signal = "approved"
        row.status = "approved"
    row.chosen_option = chosen
    if action == "always":
        row.remember_as_rule = True
        _add_standing(db, row, chosen)
    _apply_llm_approval(db, row, chosen)
    record_decision(db, row.date, row.kind, row.context, _option_ids(row), chosen, signal)
    db.commit()
    return row


def _add_standing(db: Session, row: Approval, chosen: str | None) -> None:
    match = {k: v for k, v in (row.context or {}).items() if v is not None}
    action = {"choice": chosen} if chosen else {"choice": "top"}
    db.add(StandingApprovalRow(match=match, action=action))


def _apply_llm_approval(db: Session, row: Approval, chosen: str | None) -> None:
    if row.status != "approved" and row.kind != "classification":
        return
    if row.kind == "prep_reminder":
        rule_service.add_prep_rule(db, row.proposed_change)
    elif row.kind == "classification":
        _remember_classification(db, row, chosen)
    elif row.kind == "contradiction":
        rule_service.resolve_contradiction(db, row.proposed_change, chosen)


def _remember_classification(db: Session, row: Approval, chosen: str | None) -> None:
    change = row.proposed_change
    accepted = row.status == "approved" and chosen != "meeting"
    classified_as = change["classified_as"] if accepted else "meeting"
    meal_type = change["meal_type"] if accepted else None
    db.merge(
        TitleClassification(
            title_normalized=change["title"], classified_as=classified_as, meal_type=meal_type
        )
    )
    change = {**change, "classified_as": classified_as, "meal_type": meal_type}
    row.proposed_change = change


def undo(db: Session, log_id: int) -> ActivityLog:
    """Undo an auto-applied change and record it as a negative signal."""
    row = db.get(ActivityLog, log_id)
    if row is None:
        raise ValueError("That log entry no longer exists.")
    if row.undone:
        return row
    row.undone = True
    change = row.change or {}
    rule_id = change.get("rule_id")
    if rule_id:
        rule = db.get(Rule, rule_id)
        if rule:
            rule.status = "archived"
    option_ids = [o.get("id") for o in change.get("options", [])]
    record_decision(
        db, row.date, change.get("kind", "auto"), change.get("context", {}),
        option_ids, change.get("option"), "undone",
    )  # fmt: skip
    db.commit()
    return row
