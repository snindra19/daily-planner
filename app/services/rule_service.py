"""Create, confirm, edit, and delete rules. LLM output stays a draft until confirmed."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.llm import tasks
from app.llm.client import LLMClient, LLMError, RecordingMissing
from app.models import (
    Approval,
    CommitmentRow,
    FoodOptionRow,
    Question,
    QuotaRow,
    Rule,
)
from app.scheduler.conflicts import find_indirect_conflicts
from app.schemas.llm import DraftPayload
from app.schemas.rules import EventRule, slugify
from app.schemas.scheduling import Commitment, Conflict
from app.services.profile_service import get_profile, update_changes

_draft = TypeAdapter(DraftPayload)
KINDS = {"event", "series", "constraint", "preference"}


def _confirmed_context(db: Session) -> list[dict[str, Any]]:
    rows = db.query(Rule).filter_by(status="confirmed").order_by(Rule.id).all()
    return [{"rule_id": r.id, "raw_text": r.raw_text, **r.payload} for r in rows]


def parse_text(db: Session, text: str, llm: LLMClient, only_date: str | None = None) -> list[Rule]:
    """Turn text into draft rule cards, with follow-up questions and medicine suggestions."""
    result = tasks.parse(llm, text, _confirmed_context(db))
    drafts: list[Rule] = []
    for parsed in result.rules:
        payload = parsed.payload.model_dump(exclude_none=True)
        if only_date:
            if payload["type"] == "day_override":
                payload["date"] = payload.get("date") or only_date
            elif payload["type"] in ("event", "series", "constraint"):
                payload["only_date"] = only_date
        rule = Rule(
            raw_text=parsed.raw_text or text,
            payload=payload,
            kind=payload["type"],
            status="draft",
            source="user",
            confidence=parsed.confidence,
            needs_clarification=parsed.needs_clarification,
        )
        db.add(rule)
        drafts.append(rule)
    db.flush()
    for rule in drafts:
        _attach_suggestion(db, rule, llm, text, drafts)
    _generate_questions(db, text, drafts, llm)
    db.commit()
    return drafts


def _attach_suggestion(
    db: Session, rule: Rule, llm: LLMClient, text: str, drafts: list[Rule]
) -> None:
    """Suggest timing from the user's words. A suggestion naming an unknown meal is dropped."""
    payload = rule.payload
    if payload.get("category") != "medicine" or payload.get("timing_confirmed", True):
        return
    confirmed = _confirmed_context(db)
    draft_payloads = [d.payload for d in drafts]
    try:
        suggestion = tasks.medicine(llm, payload["label"], confirmed, text, draft_payloads)
    except LLMError:
        return
    meals = {p.get("id") for p in [*confirmed, *draft_payloads] if p.get("category") == "meal"}
    if all(dose.after in meals for dose in suggestion.doses):
        rule.suggestion = suggestion.model_dump()


def _generate_questions(db: Session, text: str, drafts: list[Rule], llm: LLMClient) -> None:
    try:
        found = tasks.questions(llm, text, [r.payload for r in drafts], _confirmed_context(db))
    except LLMError:
        return
    known = {q.question_key for q in db.query(Question).all()}
    for question in found.questions:
        if question.key in known:
            continue
        db.add(
            Question(
                question_key=question.key,
                text=question.text,
                condition=question.condition,
                depends_on=question.depends_on,
            )
        )


def confirm_rule(db: Session, rule_id: int, llm: LLMClient | None) -> list[Approval]:
    """Confirm a draft. A direct contradiction keeps it a draft and asks the user to pick."""
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise ValueError("That rule no longer exists.")
    found = check_contradictions(db, rule, llm) if llm else []
    if found:
        db.commit()
        return found
    _apply_confirmed(db, rule)
    db.commit()
    return []


def check_contradictions(db: Session, rule: Rule, llm: LLMClient) -> list[Approval]:
    """Ask the LLM about direct contradictions and create one approval per contradiction."""
    existing = [c for c in _confirmed_context(db) if c["rule_id"] != rule.id]
    if not existing or rule.payload.get("type") not in KINDS:
        return []
    try:
        result = tasks.contradictions(llm, rule.payload, existing)
    except RecordingMissing:
        return []
    except LLMError:
        return []
    rows: list[Approval] = []
    for item in result.contradictions:
        other = db.get(Rule, item.existing_rule_id)
        if other is None or other.status != "confirmed":
            continue
        row = Approval(
            date="",
            key=f"contradiction:{rule.id}:{other.id}",
            kind="contradiction",
            origin="llm",
            summary=f'"{rule.raw_text}" contradicts "{other.raw_text}"',
            reason=item.explanation,
            proposed_change={
                "id": "keep_new",
                "label": f'Keep "{rule.raw_text}"',
                "new_rule_id": rule.id,
                "existing_rule_id": other.id,
            },
            alternatives=[{"id": "keep_old", "label": f'Keep "{other.raw_text}"'}],
            context={"kind": "contradiction"},
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def resolve_contradiction(db: Session, change: dict[str, Any], chosen: str | None) -> None:
    """Apply the user's pick. The rule not picked is archived."""
    new_rule = db.get(Rule, change["new_rule_id"])
    old_rule = db.get(Rule, change["existing_rule_id"])
    if chosen == "keep_old":
        if new_rule:
            new_rule.status = "archived"
        return
    if old_rule:
        old_rule.status = "archived"
    if new_rule:
        _apply_confirmed(db, new_rule)


def _apply_confirmed(db: Session, rule: Rule) -> None:
    payload = dict(rule.payload)
    kind = payload.get("type")
    if kind == "commitment":
        db.add(CommitmentRow(**{k: v for k, v in payload.items() if k != "type"}))
    elif kind == "food_option":
        db.add(FoodOptionRow(**{k: v for k, v in payload.items() if k != "type"}, approved=True))
    elif kind == "quota":
        _apply_quota(db, payload)
    elif kind == "profile_setting":
        _apply_profile(db, payload)
    elif kind == "day_override":
        _apply_day_override(db, payload)
    rule.status = "confirmed"
    db.flush()


def _apply_quota(db: Session, payload: dict[str, Any]) -> None:
    db.add(
        QuotaRow(
            item_label=payload["item_label"],
            target_per_week=payload["target_per_week"],
            max_misses_per_month=payload.get("max_misses_per_month", 0),
        )
    )
    item = EventRule(
        id=slugify(payload["item_label"]),
        label=payload["item_label"],
        category="quota",
        duration_minutes=payload.get("duration_minutes", 60),
        location=payload.get("location"),
    )
    db.add(
        Rule(
            raw_text=f"{payload['item_label']} (quota item)",
            payload=item.model_dump(exclude_none=True),
            kind="event",
            status="confirmed",
        )
    )


def _apply_profile(db: Session, payload: dict[str, Any]) -> None:
    profile = get_profile(db)
    if payload.get("default_wake_by_weekday"):
        profile.default_wake_by_weekday = {
            **(profile.default_wake_by_weekday or {}),
            **payload["default_wake_by_weekday"],
        }
    if payload.get("sleep_time"):
        profile.sleep_time = payload["sleep_time"]


def _apply_day_override(db: Session, payload: dict[str, Any]) -> None:
    values: dict[str, Any] = {}
    if payload.get("no_commitments_after"):
        values["no_commitments_after"] = payload["no_commitments_after"]
    if payload.get("removed_commitments"):
        values["removed_commitments"] = payload["removed_commitments"]
    update_changes(db, payload["date"], **values)


def accept_suggestion(db: Session, rule_id: int) -> list[Rule]:
    """Turn a confirmed medicine suggestion into one dose rule per suggested meal."""
    rule = db.get(Rule, rule_id)
    if rule is None or not rule.suggestion:
        raise ValueError("That medicine has no suggestion to accept.")
    payload = rule.payload
    base = payload.get("id") or slugify(payload["label"])
    suggestion = rule.suggestion
    doses: list[Rule] = []
    total = len(suggestion["doses"])
    for number, dose in enumerate(suggestion["doses"], start=1):
        item = EventRule(
            id=f"{base}_{number}",
            label=f"{payload['label']} {number}/{total}",
            category="medicine",
            anchor={"ref": "event", "id": dose["after"]},
            after_end=True,
            requires_food=suggestion.get("requires_food", True),
            food_source_ref=dose["after"],
            min_gap_minutes=suggestion.get("min_gap_minutes"),
            dose_group=base,
            allowed_during_busy=True,
            duration_minutes=1,
        )
        row = Rule(
            raw_text=f"{payload['label']} after {dose['after']}",
            payload=item.model_dump(exclude_none=True),
            kind="event",
            status="confirmed",
            source="ai_suggested",
        )
        db.add(row)
        doses.append(row)
    rule.status = "archived"
    db.commit()
    return doses


def add_prep_rule(db: Session, change: dict[str, Any]) -> Rule:
    """Add an accepted prep reminder as a rule for that date."""
    depends = change.get("depends_on") or [None]
    rule = Rule(
        raw_text=change.get("reason", ""),
        payload=change["rule"],
        kind="event",
        status="confirmed",
        source="ai_suggested",
        suggestion={"prep_event": depends[0]},
    )
    db.add(rule)
    db.flush()
    return rule


def dependents_of(db: Session, rule: Rule) -> list[Rule]:
    """Return confirmed rules anchored to or fed by this rule."""
    target = rule.payload.get("id")
    found = []
    for other in db.query(Rule).filter_by(status="confirmed").all():
        if other.id == rule.id:
            continue
        anchor = other.payload.get("anchor") or {}
        if target and target in (anchor.get("id"), other.payload.get("food_source_ref")):
            found.append(other)
    return found


def delete_rule(db: Session, rule_id: int, force: bool = False) -> list[Rule]:
    """Archive a rule. Returns its dependents, without deleting, unless forced."""
    rule = db.get(Rule, rule_id)
    if rule is None:
        return []
    dependents = dependents_of(db, rule)
    if dependents and not force:
        return dependents
    rule.status = "archived"
    db.commit()
    return []


def update_rule(db: Session, rule_id: int, payload: dict[str, Any], raw_text: str | None) -> Rule:
    """Validate and save an edited payload. An edited confirmed rule goes back to draft."""
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise ValueError("That rule no longer exists.")
    rule.payload = _draft.validate_python(payload).model_dump(exclude_none=True)
    rule.kind = rule.payload["type"]
    if raw_text is not None:
        rule.raw_text = raw_text
    rule.status = "draft"
    db.commit()
    return rule


def indirect_conflicts(db: Session) -> list[Conflict]:
    """Check confirmed rules against commitments on every weekday."""
    rules = [
        EventRule(**r.payload)
        for r in db.query(Rule).filter_by(status="confirmed", kind="event").order_by(Rule.id).all()
    ]
    commitments = [
        Commitment(
            id=str(c.id),
            label=c.label,
            days_of_week=c.days_of_week or [],
            start=c.start,
            end=c.end,
            commute_minutes=c.commute_minutes or 0,
        )
        for c in db.query(CommitmentRow).all()
    ]
    conflicts: dict[str, Conflict] = {}
    for weekday in range(7):
        for conflict in find_indirect_conflicts(rules, commitments, weekday):
            conflicts[conflict.message] = conflict
    return list(conflicts.values())


def answer_question(db: Session, question_id: int, answer: str, llm: LLMClient | None) -> Question:
    """Save an answer. A changed answer makes dependent questions obsolete, with their records."""
    question = db.get(Question, question_id)
    if question is None:
        raise ValueError("That question no longer exists.")
    changed = question.answer is not None and question.answer != answer
    question.answer = answer
    question.status = "answered"
    if changed:
        _archive_records(db, question)
        _obsolete_dependents(db, question)
    if llm:
        _parse_answer(db, question, answer, llm)
    db.commit()
    return question


def _parse_answer(db: Session, question: Question, answer: str, llm: LLMClient) -> None:
    text = f"{question.text} {answer}"
    try:
        drafts = parse_text(db, text, llm)
    except LLMError:
        return
    question.created_records = [d.id for d in drafts]


def _archive_records(db: Session, question: Question) -> None:
    for rule_id in question.created_records or []:
        rule = db.get(Rule, rule_id)
        if rule:
            rule.status = "archived"
    question.created_records = []


def _obsolete_dependents(db: Session, question: Question) -> None:
    for other in db.query(Question).all():
        if question.question_key in (other.depends_on or []) and other.status != "obsolete":
            other.status = "obsolete"
            _archive_records(db, other)
            _obsolete_dependents(db, other)
