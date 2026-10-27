"""Shared helpers: load scenario fixtures and call the scheduler."""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.scheduler.engine import build_plan
from app.schemas.rules import RulePayload
from app.schemas.scheduling import (
    CalendarEvent,
    Commitment,
    DayOverrides,
    Decision,
    FoodOption,
    PlanResult,
    ProfileInput,
    Quota,
    QuotaRecord,
    StandingApproval,
    to_hhmm,
)

SCENARIOS = Path(__file__).resolve().parent.parent / "fixtures" / "scenarios"
_rules = TypeAdapter(list[RulePayload])


def load(name: str) -> dict[str, Any]:
    """Load a scenario fixture by file stem."""
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def plan(data: dict[str, Any], **changes: Any) -> PlanResult:
    """Build a plan from a fixture dict. Keyword arguments override the fixture's values."""
    merged = {**data, **changes}
    profile = {
        "approval_mode": merged.get("approval_mode", "restricted"),
        "sleep_time": merged.get("sleep_time", "23:00"),
    }
    if "weights" in merged:
        profile["weights"] = merged["weights"]
    profile.update(merged.get("profile", {}))
    decisions = {k: Decision(**v) for k, v in merged.get("decisions", {}).items()}
    return build_plan(
        date=Date.fromisoformat(merged["date"]),
        wake_time=merged["wake"],
        now=merged.get("now"),
        rules=_rules.validate_python(merged.get("rules", [])),
        commitments=[Commitment(**c) for c in merged.get("commitments", [])],
        food_options=[FoodOption(**f) for f in merged.get("food_options", [])],
        quotas=[Quota(**q) for q in merged.get("quotas", [])],
        quota_history=[QuotaRecord(**r) for r in merged.get("quota_history", [])],
        calendar_events=[CalendarEvent(**e) for e in merged.get("calendar_events", [])],
        day_overrides=DayOverrides(**merged.get("day_overrides", {})),
        item_status=merged.get("item_status", {}),
        standing_approvals=[StandingApproval(**s) for s in merged.get("standing_approvals", [])],
        approved_decisions=decisions,
        profile=ProfileInput(**profile),
    )


def rows(result: PlanResult) -> list[list[str]]:
    """Return [start, label] for every displayed row."""
    return [[to_hhmm(r.start), r.label] for r in result.rows]


def at(result: PlanResult, key: str) -> str:
    """Return an item's start time as HH:MM."""
    item = result.item(key)
    assert item is not None, f"{key} is not in the plan: {[i.key for i in result.items]}"
    return to_hhmm(item.start)
