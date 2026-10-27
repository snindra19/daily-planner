"""POST, PUT, and DELETE routes. They redirect back to a page, which recomputes the plan."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.calendar.base import CalendarSource
from app.db import get_db
from app.deps import get_calendar, get_llm, get_now, tomorrow_of
from app.llm.client import LLMClient, LLMError
from app.models import (
    CommitmentRow,
    FoodOptionRow,
    ItemStatus,
    QuotaRow,
    Rule,
    StandingApprovalRow,
)
from app.routes.pages import profile_context, templates
from app.schemas.scheduling import DEFAULT_WEIGHTS
from app.services import (
    approval_service,
    calendar_service,
    learning_service,
    plan_service,
    rule_service,
)
from app.services.profile_service import get_changes, get_profile, update_changes

router = APIRouter()


def _go(path: str, msg: str | None = None) -> RedirectResponse:
    target = f"{path}?msg={quote(msg)}" if msg else path
    return RedirectResponse(target, status_code=303)


def _back(request: Request, default: str = "/", msg: str | None = None) -> RedirectResponse:
    """Redirect to the page the request came from. Only the path of the referer is used."""
    referer = urlparse(request.headers.get("referer", ""))
    path = referer.path if referer.path.startswith("/") else default
    return _go(path or default, msg)


def _status_row(db: Session, iso: str, key: str) -> ItemStatus:
    row = db.query(ItemStatus).filter_by(date=iso, item_key=key).first()
    if row is None:
        row = ItemStatus(date=iso, item_key=key, status="pending")
        db.add(row)
    return row


@router.post("/wake")
def set_wake(
    request: Request,
    date_iso: str = Form(..., alias="date"),
    kind: str = Form(...),
    time: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Enter tomorrow's target or today's actual wake-up."""
    row = plan_service.get_day_plan(db, date_iso)
    if kind == "actual":
        row.actual_wake = time
    else:
        row.target_wake = time
    db.commit()
    return _back(request)


@router.post("/late/override")
def late_override(
    db: Session = Depends(get_db), now: datetime = Depends(get_now)
) -> RedirectResponse:
    """Restart the day at the current time."""
    iso = now.date().isoformat()
    plan_service.get_day_plan(db, iso).late_mode = "override"
    update_changes(db, iso, late_mode="override", restart_at=now.strftime("%H:%M"))
    db.commit()
    return _go("/")


@router.post("/late/continue")
def late_continue(
    done: str = Form(""),
    not_done: str = Form(""),
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Save the Done and Not done lists and re-plan only the Not done items."""
    iso = now.date().isoformat()
    for status, keys in (("done", done), ("not_done", not_done)):
        for key in filter(None, keys.split(",")):
            _status_row(db, iso, key).status = status
    plan_service.get_day_plan(db, iso).late_mode = "continue"
    update_changes(db, iso, late_mode="continue", restart_at=now.strftime("%H:%M"))
    db.commit()
    return _go("/")


@router.post("/rules/parse", response_class=HTMLResponse)
def parse_rules(
    request: Request,
    text: str = Form(...),
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
) -> Response:
    """Turn free text into draft rule cards and return the updated cards."""
    message = None
    if llm is None:
        message = "The LLM is not available. Check LLM_MODE and ANTHROPIC_API_KEY."
    else:
        try:
            rule_service.parse_text(db, text, llm)
        except LLMError as error:
            message = str(error)
    context = profile_context(db)
    context["parse_error"] = message
    return templates.TemplateResponse(request, "_drafts.html", context)


@router.post("/tomorrow/extra")
def tomorrow_extra(
    text: str = Form(...),
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Parse "anything else for tomorrow?" into draft cards for that date."""
    if llm is None:
        return _go("/tomorrow", "The LLM is not available.")
    try:
        rule_service.parse_text(db, text, llm, only_date=tomorrow_of(now).isoformat())
    except LLMError as error:
        return _go("/tomorrow", str(error))
    return _go("/tomorrow")


@router.post("/tomorrow/signoff")
def sign_off(
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Finish the night review, then look for new learned patterns."""
    row = plan_service.get_day_plan(db, tomorrow_of(now).isoformat())
    row.reviewed_at = datetime.now(UTC)
    db.commit()
    error = learning_service.analyze(db, now.date(), llm) if llm else None
    return _go("/tomorrow", error)


@router.post("/rules/{rule_id}/confirm")
def confirm_rule(
    request: Request,
    rule_id: int,
    accept_suggestion: str = Form(""),
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
) -> RedirectResponse:
    """Confirm a draft or a medicine suggestion. Contradictions wait for the user's pick."""
    if accept_suggestion:
        rule_service.accept_suggestion(db, rule_id)
        return _back(request, "/profile")
    found = rule_service.confirm_rule(db, rule_id, llm)
    if found:
        return _back(request, "/profile", "This contradicts a rule you have. Pick one below.")
    return _back(request, "/profile")


@router.put("/rules/{rule_id}")
def edit_rule(
    rule_id: int,
    payload: str = Form(...),
    raw_text: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    """Save an edited rule card."""
    try:
        rule_service.update_rule(db, rule_id, json.loads(payload), raw_text)
    except (ValueError, ValidationError) as error:
        return Response(headers={"HX-Redirect": f"/profile?msg={quote(_short(error))}"})
    return Response(headers={"HX-Redirect": "/profile"})


def _short(error: Exception) -> str:
    return f"That rule is not valid: {str(error).splitlines()[0]}"


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, force: bool = False, db: Session = Depends(get_db)) -> Response:
    """Delete a rule, warning first when other rules depend on it."""
    dependents = rule_service.delete_rule(db, rule_id, force)
    if dependents:
        names = ", ".join(d.payload.get("label", str(d.id)) for d in dependents)
        message = f"Other rules depend on this one: {names}. Delete again from each of them first."
        return Response(headers={"HX-Redirect": f"/profile?msg={quote(message)}"})
    return Response(headers={"HX-Redirect": "/profile"})


@router.post("/questions/{question_id}/answer")
def answer_question(
    question_id: int,
    answer: str = Form(...),
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
) -> RedirectResponse:
    """Save a follow-up answer."""
    rule_service.answer_question(db, question_id, answer, llm)
    return _go("/profile")


@router.post("/profile/priorities")
def save_priorities(order: str = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    """Save the drag and drop priority order."""
    get_profile(db).priority_order = order.split(",")
    db.commit()
    return _go("/profile")


@router.post("/profile/settings")
async def save_settings(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Save usual wake-up times, sleep time, night review time, and weights."""
    form = await request.form()
    profile = get_profile(db)
    wake = dict(profile.default_wake_by_weekday or {})
    for weekday in range(7):
        value = form.get(f"wake_{weekday}")
        if value:
            wake[str(weekday)] = str(value)
    profile.default_wake_by_weekday = wake
    profile.sleep_time = str(form.get("sleep_time") or profile.sleep_time)
    profile.night_review_time = str(form.get("night_review_time") or profile.night_review_time)
    weights = dict(profile.weights or {})
    for name in DEFAULT_WEIGHTS:
        value = form.get(f"weight_{name}")
        if value not in (None, ""):
            weights[name] = float(str(value))
    profile.weights = weights
    db.commit()
    return _go("/profile")


@router.post("/profile/mode")
def set_mode(
    request: Request,
    mode: str = Form(...),
    date_iso: str | None = Form(None, alias="date"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Set the approval mode globally or for one date. Empty mode clears a date's override."""
    if mode not in ("restricted", "unrestricted", ""):
        return _back(request, "/profile", "Unknown mode.")
    if date_iso:
        update_changes(db, date_iso, approval_mode=mode or None)
    elif mode:
        get_profile(db).approval_mode = mode
    db.commit()
    return _back(request, "/profile")


@router.post("/commitments")
def add_commitment(
    label: str = Form(...),
    days: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    location: str = Form(""),
    commute_minutes: int = Form(0),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Add a commitment. Days are comma-separated, 0 for Monday."""
    weekdays = [int(d) for d in days.split(",") if d.strip()]
    db.add(
        CommitmentRow(
            label=label,
            days_of_week=weekdays,
            start=start,
            end=end,
            location=location or None,
            commute_minutes=commute_minutes,
        )
    )
    db.commit()
    return _go("/profile")


@router.post("/commitments/{row_id}/delete")
def delete_commitment(row_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Delete a commitment."""
    db.query(CommitmentRow).filter_by(id=row_id).delete()
    db.commit()
    return _go("/profile")


@router.post("/foods")
def add_food(
    label: str = Form(...),
    duration_minutes: int = Form(15),
    allowed_during_busy: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Add an approved food option."""
    db.add(
        FoodOptionRow(
            label=label,
            duration_minutes=duration_minutes,
            allowed_during_busy=bool(allowed_during_busy),
            approved=True,
        )
    )
    db.commit()
    return _go("/profile")


@router.post("/foods/{row_id}/delete")
def delete_food(row_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Delete a food option."""
    db.query(FoodOptionRow).filter_by(id=row_id).delete()
    db.commit()
    return _go("/profile")


@router.post("/quotas/{row_id}/delete")
def delete_quota(row_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Delete a quota. The item's rule stays, and it stops counting toward a target."""
    db.query(QuotaRow).filter_by(id=row_id).delete()
    db.commit()
    return _go("/profile")


@router.post("/items/reorder")
def reorder_item(
    request: Request,
    item_key: str = Form(...),
    after: str = Form(...),
    date_iso: str | None = Form(None, alias="date"),
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Place an item after another for this date only."""
    iso = date_iso or now.date().isoformat()
    moves = [m for m in get_changes(db, iso).get("reorder", []) if m["item_key"] != item_key]
    update_changes(db, iso, reorder=[*moves, {"item_key": item_key, "after": after}])
    approval_service.record_decision(
        db, iso, "reorder", {"kind": "reorder", "item": item_key}, [], after, "manual_edit"
    )
    db.commit()
    return _back(request)


@router.post("/items/{key}/status")
def set_item_status(
    request: Request,
    key: str,
    status: str = Form(...),
    confirm: str = Form(""),
    date_iso: str | None = Form(None, alias="date"),
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Mark Done, Not done, or skip. A skip over the miss limit needs a second answer."""
    day = date.fromisoformat(date_iso) if date_iso else now.date()
    iso = day.isoformat()
    if status == "skipped_by_user" and not confirm:
        result = plan_service.compute_plan(db, day).result
        if key in result.skip_warnings:
            return RedirectResponse(f"/?skip_warn={quote(key, safe='')}", status_code=303)
    _status_row(db, iso, key).status = status
    if status == "skipped_by_user":
        context = {"kind": "skip", "item": key}
        approval_service.record_decision(db, iso, "skip", context, [], key, "manual_skip")
    db.commit()
    return _back(request)


@router.post("/items/{key}/alarm")
def set_alarm(
    request: Request,
    key: str,
    alarm_mode: str = Form(...),
    date_iso: str | None = Form(None, alias="date"),
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Store the alarm or reminder setting for an item."""
    iso = date_iso or now.date().isoformat()
    _status_row(db, iso, key).alarm_mode = "alarm" if alarm_mode == "alarm" else "reminder"
    db.commit()
    return _back(request)


@router.post("/approvals/{approval_id}")
def answer_approval(
    request: Request,
    approval_id: int,
    action: str = Form(...),
    option_id: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve, reject, always, or pick an alternative."""
    try:
        approval_service.decide(db, approval_id, action, option_id)
    except ValueError as error:
        return _back(request, "/approvals", str(error))
    return _back(request, "/approvals")


@router.post("/standing/{row_id}/revoke")
def revoke_standing(row_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Remove a standing approval."""
    db.query(StandingApprovalRow).filter_by(id=row_id).delete()
    db.commit()
    return _go("/approvals")


@router.post("/calendar/refresh")
def refresh_calendar(
    request: Request,
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    source: CalendarSource = Depends(get_calendar),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Re-fetch today's and tomorrow's events. The page recomputes and shows the diff."""
    calendar_service.sync(db, tomorrow_of(now), source, llm)
    return _back(request)


@router.post("/activity/{log_id}/undo")
def undo_activity(log_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Undo an auto-applied change."""
    try:
        approval_service.undo(db, log_id)
    except ValueError as error:
        return _go("/activity", str(error))
    return _go("/activity")


@router.post("/learned/analyze")
def analyze_now(
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    now: datetime = Depends(get_now),
) -> RedirectResponse:
    """Run pattern analysis now."""
    if llm is None:
        return _go("/learned", "The LLM is not available.")
    return _go("/learned", learning_service.analyze(db, now.date(), llm))


@router.post("/learned/{pref_id}")
def set_learned(
    pref_id: int, action: str = Form(...), db: Session = Depends(get_db)
) -> RedirectResponse:
    """Accept or revoke a learned preference."""
    try:
        learning_service.set_status(db, pref_id, action)
    except ValueError as error:
        return _go("/learned", str(error))
    return _go("/learned")


__all__ = ["router", "Rule"]
