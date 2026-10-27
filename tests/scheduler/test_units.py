"""Unit tests: DAG, series, selectors, placement, min gap, quotas, late open, sleep time."""

from datetime import date

import pytest

from app.scheduler.dag import RuleCycleError, compute_ideals
from app.scheduler.diff import diff_snapshots
from app.scheduler.quotas import quota_state
from app.schemas.rules import Anchor, EventRule, Recurrence, SeriesRule
from app.schemas.scheduling import Quota, QuotaRecord
from tests.helpers import at, load, plan

DAY = "2026-11-02"


def event(rule_id: str, **fields) -> dict:
    return {"type": "event", "id": rule_id, "label": rule_id.title(), **fields}


WAKE_ANCHOR = {"ref": "wake_up"}


def test_cycle_is_rejected_with_rule_ids() -> None:
    first = EventRule(id="a", label="A", anchor=Anchor(ref="event", id="b"))
    second = EventRule(id="b", label="B", anchor=Anchor(ref="event", id="a"))
    with pytest.raises(RuleCycleError) as caught:
        compute_ideals([first, second], 420, {}, {})
    assert caught.value.rule_ids == ["a", "b"]


def test_series_expansion_and_labels() -> None:
    anchor = EventRule(id="base", label="Base", anchor=Anchor(ref="wake_up"), duration_minutes=1)
    drops = SeriesRule(
        id="drops",
        label="Drops",
        anchor=Anchor(ref="event", id="base"),
        recurrence=Recurrence(interval_minutes=60, count=2),
    )
    items, _ = compute_ideals([anchor, drops], 480, {}, {})
    assert [items["drops#1"].start, items["drops#2"].start] == [540, 600]
    assert items["drops#2"].label == "Drops 2/2"


def test_at_anchor_series_starts_with_the_anchor() -> None:
    anchor = EventRule(id="base", label="Base", anchor=Anchor(ref="wake_up"))
    drops = SeriesRule(
        id="drops",
        label="Drops",
        anchor=Anchor(ref="event", id="base"),
        recurrence=Recurrence(interval_minutes=60, count=2, start_mode="at_anchor"),
    )
    items, _ = compute_ideals([anchor, drops], 480, {}, {})
    assert items["drops#1"].start == 480


@pytest.mark.parametrize("selector,expected", [("first", 480 + 60), ("last", 480 + 120)])
def test_occurrence_selector(selector: str, expected: int) -> None:
    anchor = EventRule(id="base", label="Base", anchor=Anchor(ref="wake_up"))
    pills = SeriesRule(
        id="pills",
        label="Pills",
        anchor=Anchor(ref="event", id="base"),
        recurrence=Recurrence(interval_minutes=60, count=2),
    )
    meal = EventRule(
        id="meal", label="Meal", anchor=Anchor(ref="occurrence", id="pills", selector=selector)
    )
    items, _ = compute_ideals([anchor, pills, meal], 480, {}, {})
    assert items["meal"].start == expected


def test_nth_occurrence_selector() -> None:
    anchor = EventRule(id="base", label="Base", anchor=Anchor(ref="wake_up"))
    pills = SeriesRule(
        id="pills",
        label="Pills",
        anchor=Anchor(ref="event", id="base"),
        recurrence=Recurrence(interval_minutes=60, count=3),
    )
    meal = EventRule(
        id="meal", label="Meal", anchor=Anchor(ref="occurrence", id="pills", selector="nth", n=2)
    )
    items, _ = compute_ideals([anchor, pills, meal], 480, {}, {})
    assert items["meal"].start == 600


def test_same_inputs_give_the_same_output() -> None:
    data = load("golden_16_5")
    assert plan(data).model_dump() == plan(data).model_dump()


def test_doses_never_closer_than_min_gap() -> None:
    rules = [
        event(
            "dose_a", category="medicine", anchor=WAKE_ANCHOR, min_gap_minutes=300, dose_group="m"
        ),
        event(
            "dose_b",
            category="medicine",
            anchor={"ref": "absolute", "time": "10:00"},
            min_gap_minutes=300,
            dose_group="m",
        ),
    ]
    result = plan({"date": DAY, "wake": "08:00", "rules": rules})
    assert at(result, "dose_b") == "13:00"
    assert result.pending_approvals[0].kind == "spacing"


def test_rejected_spacing_leaves_the_dose_unslotted_not_crammed() -> None:
    rules = [
        event(
            "dose_a", category="medicine", anchor=WAKE_ANCHOR, min_gap_minutes=300, dose_group="m"
        ),
        event(
            "dose_b",
            category="medicine",
            anchor={"ref": "absolute", "time": "10:00"},
            min_gap_minutes=300,
            dose_group="m",
        ),
    ]
    decisions = {"spacing:dose_b": {"rejected": True}}
    result = plan({"date": DAY, "wake": "08:00", "rules": rules, "decisions": decisions})
    assert result.item("dose_b") is None
    assert result.unslotted[0].key == "dose_b"


def test_unconfirmed_medicine_is_not_scheduled() -> None:
    rules = [event("new_med", category="medicine", anchor=WAKE_ANCHOR, timing_confirmed=False)]
    result = plan({"date": DAY, "wake": "08:00", "rules": rules})
    assert result.item("new_med") is None
    assert result.needs_confirmation == ["New_Med"]


def test_quota_counts_week_and_month() -> None:
    quota = Quota(item_label="Gym", target_per_week=2, max_misses_per_month=1)
    history = [
        QuotaRecord(date="2026-11-02", item_label="Gym", status="done"),
        QuotaRecord(date="2026-11-03", item_label="Gym", status="done"),
        QuotaRecord(date="2026-10-30", item_label="Gym", status="not_done"),
        QuotaRecord(date="2026-11-01", item_label="Gym", status="not_done"),
    ]
    state = quota_state(quota, history, date(2026, 11, 4))
    assert state.done_this_week == 2
    assert state.weekly_target_met
    assert state.misses_this_month == 1
    assert state.miss_limit_reached


def test_gym_is_not_planned_once_the_weekly_target_is_met() -> None:
    data = load("golden_16_5")
    history = [
        {"date": "2026-11-02", "item_label": "Gym", "status": "done"},
    ]
    quotas = [{"item_label": "Gym", "target_per_week": 1, "max_misses_per_month": 1}]
    result = plan(data, quota_history=history, quotas=quotas)
    assert result.item("gym") is None
    assert not [u for u in result.unslotted if u.key == "gym"]


def test_user_skip_removes_only_that_item() -> None:
    result = plan(load("golden_16_5"), item_status={"yoga": "skipped_by_user"})
    assert result.item("yoga") is None
    assert not result.unslotted


def test_low_priority_can_be_put_ahead_of_quota_in_the_profile() -> None:
    profile = {"priority_order": ["fixed", "medicines", "meals", "low_priority", "quota"]}
    result = plan(load("golden_16_5"), profile=profile)
    assert result.item("yoga").start < result.item("gym").start


def test_diff_reports_moves_additions_and_removals() -> None:
    old = [
        {"key": "a", "label": "A", "start": 480, "end": 490},
        {"key": "b", "label": "B", "start": 500, "end": 510},
    ]
    new = [
        {"key": "a", "label": "A", "start": 540, "end": 550},
        {"key": "c", "label": "C", "start": 600, "end": 610},
    ]
    kinds = sorted(c["change"] for c in diff_snapshots(old, new))
    assert kinds == ["added", "moved", "removed"]
    assert diff_snapshots(new, new) == []
    assert diff_snapshots(None, new) == []
