"""SQLAlchemy tables, in 1.x Column style (spec section 7)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Profile(Base):
    __tablename__ = "profile"
    id = Column(Integer, primary_key=True)
    timezone = Column(String, default="America/Bogota")
    default_wake_by_weekday = Column(JSON, default=dict)
    sleep_time = Column(String, default="23:00")
    night_review_time = Column(String, default="21:00")
    priority_order = Column(JSON, default=list)
    weights = Column(JSON, default=dict)
    approval_mode = Column(String, default="restricted")


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    raw_text = Column(Text, default="")
    payload = Column(JSON, nullable=False)
    kind = Column(String, default="event")
    status = Column(String, default="draft")
    source = Column(String, default="user")
    confidence = Column(Float, nullable=True)
    needs_clarification = Column(Text, nullable=True)
    suggestion = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_now)


class CommitmentRow(Base):
    __tablename__ = "commitments"
    id = Column(Integer, primary_key=True)
    label = Column(String, nullable=False)
    days_of_week = Column(JSON, default=list)
    start = Column(String, nullable=False)
    end = Column(String, nullable=False)
    location = Column(String, nullable=True)
    commute_minutes = Column(Integer, default=0)
    in_window_kind = Column(String, default="class")
    breaks = Column(JSON, default=list)


class FoodOptionRow(Base):
    __tablename__ = "food_options"
    id = Column(Integer, primary_key=True)
    label = Column(String, nullable=False)
    duration_minutes = Column(Integer, default=15)
    allowed_during_busy = Column(Boolean, default=False)
    counts_as_meal = Column(Boolean, default=False)
    approved = Column(Boolean, default=True)


class QuotaRow(Base):
    __tablename__ = "quotas"
    id = Column(Integer, primary_key=True)
    item_label = Column(String, nullable=False)
    target_per_week = Column(Integer, default=1)
    max_misses_per_month = Column(Integer, default=0)


class DayPlan(Base):
    __tablename__ = "day_plans"
    id = Column(Integer, primary_key=True)
    date = Column(String, unique=True, nullable=False)
    target_wake = Column(String, nullable=True)
    actual_wake = Column(String, nullable=True)
    late_mode = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    plan_snapshot = Column(JSON, nullable=True)


class ItemStatus(Base):
    __tablename__ = "item_status"
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    item_key = Column(String, nullable=False)
    status = Column(String, default="pending")
    alarm_mode = Column(String, default="reminder")


class DayOverrideRow(Base):
    __tablename__ = "day_overrides"
    id = Column(Integer, primary_key=True)
    date = Column(String, unique=True, nullable=False)
    changes = Column(JSON, default=dict)


class DecisionHistory(Base):
    __tablename__ = "decision_history"
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    context = Column(JSON, default=dict)
    options_offered = Column(JSON, default=list)
    chosen = Column(JSON, nullable=True)
    signal = Column(String, nullable=False)
    created_at = Column(DateTime, default=_now)


class LearnedPreference(Base):
    __tablename__ = "learned_preferences"
    id = Column(Integer, primary_key=True)
    description = Column(Text, nullable=False)
    match = Column(JSON, default=dict)
    effect = Column(JSON, default=dict)
    evidence_count = Column(Integer, default=0)
    consistency = Column(Float, default=0)
    status = Column(String, default="proposed")
    created_at = Column(DateTime, default=_now)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    change = Column(JSON, default=dict)
    reason = Column(Text, default="")
    alternatives = Column(JSON, default=list)
    applied_by = Column(String, default="auto")
    undone = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    key = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    origin = Column(String, default="scheduler")
    summary = Column(Text, default="")
    proposed_change = Column(JSON, default=dict)
    reason = Column(Text, default="")
    alternatives = Column(JSON, default=list)
    context = Column(JSON, default=dict)
    status = Column(String, default="pending")
    chosen_option = Column(String, nullable=True)
    remember_as_rule = Column(Boolean, default=False)


class StandingApprovalRow(Base):
    __tablename__ = "standing_approvals"
    id = Column(Integer, primary_key=True)
    match = Column(JSON, default=dict)
    action = Column(JSON, default=dict)


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    question_key = Column(String, nullable=True)
    text = Column(Text, nullable=False)
    condition = Column(Text, nullable=True)
    depends_on = Column(JSON, default=list)
    answer = Column(Text, nullable=True)
    status = Column(String, default="pending")
    created_records = Column(JSON, default=list)


class CalendarCache(Base):
    __tablename__ = "calendar_cache"
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    title = Column(String, default="")
    start = Column(String, nullable=False)
    end = Column(String, nullable=False)
    location = Column(String, nullable=True)
    busy = Column(Boolean, default=True)
    all_day = Column(Boolean, default=False)
    classified_as = Column(String, default="meeting")
    meal_type = Column(String, nullable=True)


class TitleClassification(Base):
    __tablename__ = "title_classifications"
    title_normalized = Column(String, primary_key=True)
    classified_as = Column(String, nullable=False)
    meal_type = Column(String, nullable=True)
