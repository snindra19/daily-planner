"""Page routes. Routes are sync so FastAPI runs them in its thread pool."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.calendar.base import CalendarError, CalendarSource
from app.db import get_db
from app.deps import get_calendar, get_llm, get_now, tomorrow_of
from app.llm.client import LLMClient
from app.models import (
    ActivityLog,
    Approval,
    CommitmentRow,
    DayPlan,
    FoodOptionRow,
    ItemStatus,
    LearnedPreference,
    Question,
    QuotaRow,
    Rule,
    StandingApprovalRow,
)
from app.schemas.scheduling import PlanResult, to_minutes
from app.services import plan_service, prep_service, rule_service
from app.services.plan_service import PlanView
from app.services.profile_service import get_profile

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.filters["urlkey"] = lambda value: quote(str(value), safe="")


def pending_approvals(db: Session, iso: str) -> list[Approval]:
    """Return pending approvals for a date plus undated ones such as contradictions."""
    rows = db.query(Approval).filter_by(status="pending").order_by(Approval.id).all()
    return [row for row in rows if row.date in (iso, "")]


def row_views(db: Session, result: PlanResult) -> list[dict[str, Any]]:
    """Attach each row's items and alarm settings for the templates."""
    rows = db.query(ItemStatus).filter_by(date=result.date).all()
    alarms = {row.item_key: row.alarm_mode for row in rows}
    views = []
    for row in result.rows:
        items = [result.item(key) for key in row.item_keys]
        views.append({"row": row, "items": items, "alarms": alarms})
    return views


def _review_banner(db: Session, now: datetime) -> bool:
    profile = get_profile(db)
    current = now.hour * 60 + now.minute
    if current < to_minutes(profile.night_review_time):
        return False
    row = db.query(DayPlan).filter_by(date=tomorrow_of(now).isoformat()).first()
    return row is None or row.reviewed_at is None


def _base(db: Session, view: PlanView, day: Date) -> dict[str, Any]:
    return {
        "view": view,
        "day": day.isoformat(),
        "rows": row_views(db, view.result),
        "approvals": pending_approvals(db, day.isoformat()),
        "mode": view.mode,
    }


@router.get("/", response_class=HTMLResponse)
def today_page(
    request: Request,
    msg: str | None = None,
    skip_warn: str | None = None,
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    source: CalendarSource = Depends(get_calendar),
    now: datetime = Depends(get_now),
) -> HTMLResponse:
    """Today: wake-up entry, the day's list, late-open panel, and approvals."""
    day = now.date()
    view = plan_service.compute_plan(db, day, now=now, llm=llm, source=source)
    context = _base(db, view, day)
    late = view.result.late_open
    context.update(
        msg=msg,
        skip_warn=skip_warn,
        skip_message=view.result.skip_warnings.get(skip_warn or ""),
        review_banner=_review_banner(db, now),
        late_items=[view.result.item(k) for k in late.pending_keys],
        now_hhmm=now.strftime("%H:%M"),
    )
    return templates.TemplateResponse(request, "today.html", context)


@router.get("/tomorrow", response_class=HTMLResponse)
def tomorrow_page(
    request: Request,
    msg: str | None = None,
    db: Session = Depends(get_db),
    llm: LLMClient | None = Depends(get_llm),
    source: CalendarSource = Depends(get_calendar),
    now: datetime = Depends(get_now),
) -> HTMLResponse:
    """Night review: wake-up target first, then the list, extras, prep proposals, and sign-off."""
    day = tomorrow_of(now)
    view = plan_service.compute_plan(db, day, llm=llm, source=source)
    prep_error = None
    if llm is not None:
        prep_error = prep_service.run_prep(db, day, llm, view.result, view.mode)
        view = plan_service.compute_plan(db, day, llm=llm)
    context = _base(db, view, day)
    row = plan_service.get_day_plan(db, day.isoformat())
    profile = get_profile(db)
    context.update(
        msg=msg or prep_error,
        target=row.target_wake,
        reviewed=row.reviewed_at is not None,
        drafts=_drafts(db, day.isoformat()),
        global_mode=profile.approval_mode,
    )
    return templates.TemplateResponse(request, "tomorrow.html", context)


def _drafts(db: Session, only_date: str | None = None) -> list[Rule]:
    rows = db.query(Rule).filter_by(status="draft").order_by(Rule.id).all()
    if only_date is None:
        return [r for r in rows if not r.payload.get("only_date") and not r.payload.get("date")]
    return [
        r
        for only in [only_date]
        for r in rows
        if only in (r.payload.get("only_date"), r.payload.get("date"))
    ]


def profile_context(db: Session) -> dict[str, Any]:
    """Everything the Profile page and its draft partial show."""
    return {
        "profile": get_profile(db),
        "drafts": _drafts(db),
        "confirmed": db.query(Rule).filter_by(status="confirmed").order_by(Rule.id).all(),
        "questions": db.query(Question).filter_by(status="pending").order_by(Question.id).all(),
        "commitments": db.query(CommitmentRow).order_by(CommitmentRow.id).all(),
        "foods": db.query(FoodOptionRow).order_by(FoodOptionRow.id).all(),
        "quotas": db.query(QuotaRow).order_by(QuotaRow.id).all(),
        "conflicts": rule_service.indirect_conflicts(db),
        "approvals": [a for a in pending_approvals(db, "") if a.kind == "contradiction"],
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request, msg: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Setup and profile."""
    context = profile_context(db)
    context.update(msg=msg, mode=context["profile"].approval_mode)
    return templates.TemplateResponse(request, "profile.html", context)


@router.get("/approvals", response_class=HTMLResponse)
def approvals_page(
    request: Request, msg: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    """All pending approvals and the standing approvals list."""
    rows = db.query(Approval).filter_by(status="pending").order_by(Approval.date, Approval.id).all()
    context = {
        "msg": msg,
        "approvals": rows,
        "standing": db.query(StandingApprovalRow).order_by(StandingApprovalRow.id).all(),
        "mode": get_profile(db).approval_mode,
    }
    return templates.TemplateResponse(request, "approvals.html", context)


@router.get("/activity", response_class=HTMLResponse)
def activity_page(
    request: Request, msg: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Activity log of auto-applied changes."""
    rows = db.query(ActivityLog).order_by(ActivityLog.id.desc()).all()
    context = {"msg": msg, "entries": rows, "mode": get_profile(db).approval_mode}
    return templates.TemplateResponse(request, "activity.html", context)


@router.get("/learned", response_class=HTMLResponse)
def learned_page(
    request: Request, msg: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Learned preferences with their evidence."""
    rows = db.query(LearnedPreference).order_by(LearnedPreference.id.desc()).all()
    context = {"msg": msg, "prefs": rows, "mode": get_profile(db).approval_mode}
    return templates.TemplateResponse(request, "learned.html", context)


@router.get("/calendar/status")
def calendar_status(source: CalendarSource = Depends(get_calendar)) -> JSONResponse:
    """MCP server status and the tools it offers."""
    describe = getattr(source, "status", None)
    if describe is None:
        return JSONResponse({"mode": "mock", "ok": True, "tools": []})
    try:
        return JSONResponse(describe())
    except CalendarError as error:
        return JSONResponse({"mode": "mcp", "ok": False, "error": str(error), "tools": []})
