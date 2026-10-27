"""Load inputs from the database, run the scheduler, and sync the results back."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, time
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.calendar.base import CalendarSource
from app.llm import tasks
from app.llm.client import LLMClient, LLMError
from app.models import (
    ActivityLog,
    Approval,
    CommitmentRow,
    DayPlan,
    FoodOptionRow,
    ItemStatus,
    LearnedPreference,
    QuotaRow,
    Rule,
    StandingApprovalRow,
)
from app.scheduler.dag import RuleCycleError
from app.scheduler.diff import diff_snapshots
from app.scheduler.engine import build_plan
from app.schemas.rules import RulePayload, slugify
from app.schemas.scheduling import (
    Commitment,
    DayOverrides,
    Decision,
    FoodOption,
    PlanResult,
    ProfileInput,
    Proposal,
    Quota,
    QuotaRecord,
    StandingApproval,
)
from app.services import calendar_service
from app.services.calendar_service import SyncStatus
from app.services.profile_service import get_changes, get_profile

RULE_TYPES = ("event", "series", "constraint", "preference")
_rules = TypeAdapter(list[RulePayload])


@dataclass
class PlanView:
    """A computed plan and what the page needs around it."""

    result: PlanResult
    diff: list[dict[str, str]] = field(default_factory=list)
    last_diff: list[dict[str, str]] = field(default_factory=list)
    calendar: SyncStatus = field(default_factory=SyncStatus)
    mode: str = "restricted"
    wake: str = ""
    wake_level: str = "default"
    error: str | None = None


def get_day_plan(db: Session, iso: str) -> DayPlan:
    """Return the date's plan row, creating it if needed."""
    row = db.query(DayPlan).filter_by(date=iso).first()
    if row is None:
        row = DayPlan(date=iso)
        db.add(row)
        db.commit()
    return row


def wake_for(db: Session, day: Date) -> tuple[str, str]:
    """Return the wake-up time and which level it came from: actual, target, or default."""
    row = db.query(DayPlan).filter_by(date=day.isoformat()).first()
    if row and row.actual_wake:
        return row.actual_wake, "actual"
    if row and row.target_wake:
        return row.target_wake, "target"
    defaults = get_profile(db).default_wake_by_weekday or {}
    return defaults.get(str(day.weekday()), "07:00"), "default"


def active_rule_payloads(db: Session) -> list[dict[str, Any]]:
    """Return the payloads of confirmed scheduling rules, in rule id order."""
    rows = db.query(Rule).filter_by(status="confirmed").order_by(Rule.id).all()
    return [r.payload for r in rows if r.payload.get("type") in RULE_TYPES]


def learned_effects(db: Session) -> dict[str, Any]:
    """Collect the effects of active learned preferences."""
    effects: dict[str, Any] = {
        "weights": {},
        "standing": [],
        "dont_propose": [],
        "recommend_first": {},
    }
    for row in db.query(LearnedPreference).filter_by(status="active").all():
        effect = row.effect or {}
        kind = effect.get("type")
        if kind == "weight":
            effects["weights"][effect["key"]] = effect["value"]
        elif kind == "standing_approval":
            effects["standing"].append(
                StandingApproval(match=effect["match"], action=effect.get("action", {}))
            )
        elif kind == "dont_propose":
            effects["dont_propose"].append(effect["label"])
        elif kind == "recommendation":
            effects["recommend_first"][slugify(effect["item"])] = effect["text"]
    return effects


def _decisions(db: Session, iso: str, fingerprint: str) -> dict[str, Decision]:
    decisions: dict[str, Decision] = {}
    approvals = db.query(Approval).filter_by(date=iso, origin="scheduler").all()
    for row in approvals:
        if row.status == "approved":
            decisions[row.key] = Decision(choice=row.chosen_option)
        elif row.status == "rejected":
            decisions[row.key] = Decision(rejected=True)
    undone = db.query(ActivityLog).filter_by(date=iso, undone=True).all()
    for row in undone:
        change = row.change or {}
        if change.get("fingerprint") == fingerprint and change.get("key"):
            decisions.setdefault(change["key"], Decision(suppress_auto=True))
    return decisions


def _inputs(db: Session, day: Date) -> dict[str, Any]:
    iso = day.isoformat()
    profile = get_profile(db)
    effects = learned_effects(db)
    changes = get_changes(db, iso)
    known = set(DayOverrides.model_fields)
    overrides = DayOverrides(**{k: v for k, v in changes.items() if k in known})
    statuses = {row.item_key: row.status for row in db.query(ItemStatus).filter_by(date=iso).all()}
    quotas = [
        Quota(
            item_label=q.item_label,
            target_per_week=q.target_per_week,
            max_misses_per_month=q.max_misses_per_month,
        )
        for q in db.query(QuotaRow).order_by(QuotaRow.id).all()
    ]
    return {
        "rules": _rules.validate_python(active_rule_payloads(db)),
        "commitments": [
            Commitment(
                id=str(c.id),
                label=c.label,
                days_of_week=c.days_of_week or [],
                start=c.start,
                end=c.end,
                location=c.location,
                commute_minutes=c.commute_minutes or 0,
                in_window_kind=c.in_window_kind or "class",
                breaks=c.breaks or [],
            )
            for c in db.query(CommitmentRow).order_by(CommitmentRow.id).all()
        ],
        "food_options": [
            FoodOption(
                id=str(f.id),
                label=f.label,
                duration_minutes=f.duration_minutes,
                allowed_during_busy=f.allowed_during_busy,
                counts_as_meal=f.counts_as_meal,
                approved=f.approved,
            )
            for f in db.query(FoodOptionRow).order_by(FoodOptionRow.id).all()
        ],
        "quotas": quotas,
        "quota_history": _quota_history(db, quotas),
        "calendar_events": calendar_service.load_events(db, iso),
        "day_overrides": overrides,
        "item_status": statuses,
        "standing_approvals": [
            *[
                StandingApproval(match=s.match, action=s.action or {})
                for s in db.query(StandingApprovalRow).order_by(StandingApprovalRow.id).all()
            ],
            *effects["standing"],
        ],
        "profile": ProfileInput(
            sleep_time=profile.sleep_time,
            priority_order=profile.priority_order,
            weights={**(profile.weights or {}), **effects["weights"]},
            approval_mode=profile.approval_mode,
            recommend_first=effects["recommend_first"],
        ),
    }


def _quota_history(db: Session, quotas: list[Quota]) -> list[QuotaRecord]:
    labels = {slugify(q.item_label): q.item_label for q in quotas}
    return [
        QuotaRecord(date=row.date, item_label=labels[row.item_key], status=row.status)
        for row in db.query(ItemStatus).filter(ItemStatus.item_key.in_(labels)).all()
        if row.status in ("done", "not_done", "skipped_by_user")
    ]


def _fingerprint(day: Date, wake: str, inputs: dict[str, Any]) -> str:
    """Hash the plan's inputs. Undo suppresses an auto-change until this value changes."""
    payload = {
        "date": day.isoformat(),
        "wake": wake,
        "rules": [r.model_dump() for r in inputs["rules"]],
        "commitments": [c.model_dump() for c in inputs["commitments"]],
        "food": [f.model_dump() for f in inputs["food_options"]],
        "events": [e.model_dump() for e in inputs["calendar_events"]],
        "overrides": inputs["day_overrides"].model_dump(exclude={"approval_mode"}),
        "status": inputs["item_status"],
        "weights": inputs["profile"].weights,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_plan(
    db: Session,
    day: Date,
    now: time | datetime | None = None,
    llm: LLMClient | None = None,
    source: CalendarSource | None = None,
) -> PlanView:
    """Sync the calendar if a source is given, build the plan, and save what came out of it."""
    iso = day.isoformat()
    status = calendar_service.sync(db, day, source, llm) if source else SyncStatus()
    _remove_stale_prep(db, iso)
    wake, level = wake_for(db, day)
    inputs = _inputs(db, day)
    fingerprint = _fingerprint(day, wake, inputs)
    mode = inputs["day_overrides"].approval_mode or inputs["profile"].approval_mode
    try:
        result = build_plan(
            date=day,
            wake_time=wake,
            now=now,
            approved_decisions=_decisions(db, iso, fingerprint),
            **inputs,
        )
    except RuleCycleError as error:
        empty = PlanResult(date=iso, wake=0)
        return PlanView(empty, calendar=status, mode=mode, wake=wake, wake_level=level,
                        error=str(error))  # fmt: skip
    _sync_approvals(db, iso, result, llm)
    _log_auto_changes(db, iso, result, fingerprint)
    diff, last_diff = _save_snapshot(db, iso, result)
    db.commit()
    return PlanView(result, diff, last_diff, status, mode, wake, level)


def _sync_approvals(db: Session, iso: str, result: PlanResult, llm: LLMClient | None) -> None:
    """Keep the approvals table in step with the plan. Proposals that went away are removed."""
    pending = {p.key: p for p in result.pending_approvals}
    rows = db.query(Approval).filter_by(date=iso, origin="scheduler", status="pending").all()
    existing = {row.key: row for row in rows}
    for key, row in existing.items():
        if key not in pending:
            db.delete(row)
    answered = {
        row.key
        for row in db.query(Approval).filter_by(date=iso, origin="scheduler").all()
        if row.status != "pending"
    }
    for key, proposal in pending.items():
        if key in answered:
            continue
        row = existing.get(key)
        if row is None:
            row = Approval(date=iso, key=key, kind=proposal.kind, origin="scheduler")
            row.reason = _explain(proposal, llm)
            db.add(row)
        _fill_approval(row, proposal)
    db.flush()


def _fill_approval(row: Approval, proposal: Proposal) -> None:
    row.summary = proposal.summary
    row.proposed_change = proposal.chosen.model_dump()
    row.alternatives = [o.model_dump() for o in proposal.alternatives]
    row.context = proposal.context


def _explain(proposal: Proposal, llm: LLMClient | None) -> str:
    """Use the LLM's one-liner when there is one. The scheduler's own reason is the fallback."""
    if llm is None or not proposal.alternatives:
        return proposal.reason
    decision = {
        "summary": proposal.summary,
        "score": proposal.chosen.score,
        "breakdown": proposal.chosen.breakdown,
        "alternatives": [{"label": o.label, "score": o.score} for o in proposal.alternatives],
    }
    try:
        return tasks.explain(llm, decision).text
    except LLMError:
        return proposal.reason


def _log_auto_changes(db: Session, iso: str, result: PlanResult, fingerprint: str) -> None:
    """Write each auto-applied change to the activity log once."""
    rows = db.query(ActivityLog).filter_by(date=iso).all()
    logged = {
        ((r.change or {}).get("key"), (r.change or {}).get("option")): r
        for r in rows
        if not r.undone
    }
    for proposal in result.proposals:
        if proposal.state not in ("auto", "standing"):
            continue
        marker = (proposal.key, proposal.chosen.id)
        if marker in logged:
            logged[marker].change = {**logged[marker].change, "fingerprint": fingerprint}
            continue
        db.add(
            ActivityLog(
                date=iso,
                change={
                    "key": proposal.key,
                    "kind": proposal.kind,
                    "option": proposal.chosen.id,
                    "label": proposal.chosen.label,
                    "context": proposal.context,
                    "options": [o.model_dump() for o in [proposal.chosen, *proposal.alternatives]],
                    "fingerprint": fingerprint,
                },
                reason=proposal.reason,
                alternatives=[o.label for o in proposal.alternatives],
                applied_by="auto",
            )
        )


def _save_snapshot(
    db: Session, iso: str, result: PlanResult
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    row = get_day_plan(db, iso)
    items = [i.model_dump(include={"key", "label", "start", "end"}) for i in result.items]
    previous = row.plan_snapshot or {}
    diff = diff_snapshots(previous.get("items"), items)
    last_diff = diff or previous.get("last_diff", [])
    row.plan_snapshot = {"items": items, "last_diff": last_diff}
    return diff, last_diff


def _remove_stale_prep(db: Session, iso: str) -> None:
    """Remove prep proposals and prep items whose calendar event is gone."""
    live = {e.source_id for e in calendar_service.load_events(db, iso)}
    for row in db.query(Approval).filter_by(date=iso, kind="prep_reminder", status="pending").all():
        if not set((row.proposed_change or {}).get("depends_on", [])) <= live:
            db.delete(row)
    for rule in db.query(Rule).filter_by(status="confirmed", kind="event").all():
        event = (rule.suggestion or {}).get("prep_event")
        if event and rule.payload.get("only_date") == iso and event not in live:
            rule.status = "archived"
    db.flush()
