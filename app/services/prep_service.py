"""Night-review prep proposals. Times are computed by the scheduler from a relative anchor."""

from __future__ import annotations

import hashlib
import json
from datetime import date as Date
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.llm import tasks
from app.llm.client import LLMClient, LLMError
from app.models import ActivityLog, Approval, DecisionHistory, StandingApprovalRow
from app.schemas.llm import PrepProposal
from app.schemas.rules import EventRule, slugify
from app.schemas.scheduling import PlanResult
from app.services import calendar_service, rule_service
from app.services.plan_service import active_rule_payloads, learned_effects
from app.services.profile_service import get_changes, get_profile, update_changes

SIMILAR_LABEL = 80


def run_prep(db: Session, day: Date, llm: LLMClient, plan: PlanResult, mode: str) -> str | None:
    """Ask for prep proposals when the day's events changed. Returns an error message, if any."""
    iso = day.isoformat()
    events = _event_inputs(db, iso)
    if not events:
        return None
    marker = hashlib.sha256(json.dumps(events, sort_keys=True).encode("utf-8")).hexdigest()
    if get_changes(db, iso).get("prep_marker") == marker:
        return None
    effects = learned_effects(db)
    context = _context(db, plan, effects["dont_propose"])
    try:
        result = tasks.prep(llm, events, context)
    except LLMError as error:
        return str(error)
    update_changes(db, iso, prep_marker=marker)
    for proposal in result.proposals:
        if _is_blocked(proposal.label, effects["dont_propose"]):
            continue
        if not _valid_target(proposal.relative_to, plan, events):
            continue
        _add_proposal(db, iso, proposal, mode)
    db.commit()
    return None


def _event_inputs(db: Session, iso: str) -> list[dict[str, Any]]:
    return [
        {
            "source_id": e.source_id,
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "location": e.location,
        }
        for e in calendar_service.load_events(db, iso)
        if not e.all_day and e.busy
    ]


def _context(db: Session, plan: PlanResult, dont_propose: list[str]) -> dict[str, Any]:
    rejected = (
        db.query(DecisionHistory)
        .filter_by(kind="prep_reminder", signal="rejected")
        .order_by(DecisionHistory.id.desc())
        .limit(20)
        .all()
    )
    return {
        "plan": [{"key": i.key, "label": i.label, "start": i.start_hhmm} for i in plan.items],
        "item_keys": [i.key for i in plan.items],
        "rules": active_rule_payloads(db),
        "do_not_propose": dont_propose,
        "recent_rejections": [(r.context or {}).get("label") for r in rejected],
    }


def _is_blocked(label: str, dont_propose: list[str]) -> bool:
    lowered = label.lower()
    return any(fuzz.token_set_ratio(lowered, b.lower()) >= SIMILAR_LABEL for b in dont_propose)


def _valid_target(target: str, plan: PlanResult, events: list[dict[str, Any]]) -> bool:
    if plan.item(target):
        return True
    return any(target == f"cal:{e['source_id']}:leave" for e in events)


def _event_id(target: str) -> list[str]:
    if target.startswith("cal:"):
        return [target.split(":")[1]]
    return []


def _standing_allows(db: Session, label: str) -> bool:
    for row in db.query(StandingApprovalRow).all():
        match = row.match or {}
        if match.get("kind") == "prep_reminder" and match.get("label") == label:
            return True
    return False


def _add_proposal(db: Session, iso: str, proposal: PrepProposal, mode: str) -> None:
    rule = EventRule(
        id=f"prep_{slugify(proposal.label)}",
        label=proposal.label,
        category="routine",
        anchor={"ref": "event", "id": proposal.relative_to},
        offset_minutes=proposal.offset_minutes,
        duration_minutes=proposal.duration_minutes,
        allowed_during_busy=True,
        only_date=iso,
    )
    change = {
        "id": "add",
        "label": f"Add {proposal.label}",
        "rule": rule.model_dump(exclude_none=True),
        "reason": proposal.reason,
        "depends_on": _event_id(proposal.relative_to),
    }
    context = {"kind": "prep_reminder", "label": proposal.label}
    if mode == "unrestricted" or _standing_allows(db, proposal.label):
        _apply_automatically(db, iso, proposal, change, context)
        return
    key = f"prep:{slugify(proposal.label)}"
    if db.query(Approval).filter_by(date=iso, key=key).first():
        return
    db.add(
        Approval(
            date=iso,
            key=key,
            kind="prep_reminder",
            origin="llm",
            summary=f"Add {proposal.label}",
            reason=proposal.reason,
            proposed_change=change,
            context=context,
        )
    )


def _apply_automatically(
    db: Session, iso: str, proposal: PrepProposal, change: dict[str, Any], context: dict[str, Any]
) -> None:
    row = rule_service.add_prep_rule(db, change)
    db.add(
        ActivityLog(
            date=iso,
            change={
                "key": f"prep:{slugify(proposal.label)}",
                "kind": "prep_reminder",
                "option": "add",
                "label": f"Add {proposal.label}",
                "context": context,
                "rule_id": row.id,
                "item_key": change["rule"]["id"],
            },
            reason=proposal.reason,
            alternatives=[],
            applied_by="auto",
        )
    )


def night_mode(db: Session, iso: str) -> str:
    """Return the approval mode in force on a date."""
    return get_changes(db, iso).get("approval_mode") or get_profile(db).approval_mode
