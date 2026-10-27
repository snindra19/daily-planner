"""Section 16 scenarios that the pure scheduler answers on its own."""

from __future__ import annotations

from tests.helpers import at, load, plan
from tests.scenarios.test_helpers_data import (
    MEDS_ROUTINE,
    MONDAY,
    absolute,
    busy_day,
    calendar,
    with_rules,
)


def golden_5() -> dict:
    return load("golden_16_5")


def golden_6() -> dict:
    return load("golden_16_6")


def test_p6_monday_with_classes() -> None:
    data = golden_5()
    result = plan(data)
    assert [[r.start_hhmm, r.label] for r in result.rows] == data["expected_rows"]


def test_e1_override_restarts_the_day() -> None:
    late = plan(load("golden_16_4"), now="09:00")
    assert late.late_open.needed
    assert {"hygiene", "breakfast"} <= set(late.late_open.pending_keys)
    overrides = {"late_mode": "override", "restart_at": "09:00"}
    result = plan(load("golden_16_4"), now="09:00", day_overrides=overrides)
    assert at(result, "hygiene") == "09:15"
    assert at(result, "breakfast") == "10:00"
    assert not result.late_open.needed


def test_e2_continue_replans_only_not_done() -> None:
    data = {
        **MEDS_ROUTINE,
        "wake": "07:00",
        "day_overrides": {"late_mode": "continue", "restart_at": "09:00"},
        "item_status": {"breakfast": "done", "medicine_1": "not_done"},
    }
    result = plan(data)
    assert at(result, "breakfast") == "08:00"
    assert result.item("breakfast").status == "done"
    assert at(result, "medicine_1") == "09:00"
    assert result.item("medicine_1").start >= result.item("breakfast").end


def test_e3_lunch_fits_nowhere_and_meds_are_due() -> None:
    data = golden_6()
    data["rules"][3]["tolerance_minutes"] = 60
    data["calendar_events"][0]["end"] = "2026-11-02T15:00:00"
    proposal = plan(data).pending_approvals[0]
    assert proposal.kind == "fallback"
    assert proposal.chosen.id == "food:apple:during_busy"
    assert proposal.alternatives == []
    assert proposal.chosen.meal_skipped == "Lunch skipped"


def test_e4_afternoon_dose_shifts_to_keep_the_gap() -> None:
    data = {
        **MEDS_ROUTINE,
        "wake": "08:00",
        "day_overrides": {"late_mode": "continue", "restart_at": "11:00"},
        "item_status": {"breakfast": "done", "medicine_1": "not_done"},
    }
    result = plan(data)
    assert at(result, "medicine_1") == "11:00"
    assert at(result, "medicine_2") == "15:00"
    assert result.item("medicine_2").status == "proposed"
    assert [p.kind for p in result.pending_approvals] == ["spacing"]


def test_e5_full_day_with_miss_limit_reached() -> None:
    data = busy_day()
    data["quota_history"] = [{"date": "2026-11-01", "item_label": "Gym", "status": "not_done"}]
    result = plan(data)
    assert result.item("gym") is None
    assert result.unslotted[0].message == "Gym miss limit reached this month. Skip anyway?"
    assert result.skip_warnings["gym"] == "Gym miss limit reached this month. Skip anyway?"


def test_e5_promoted_gym_is_placed_when_it_fits() -> None:
    history = [{"date": "2026-11-01", "item_label": "Gym", "status": "not_done"}]
    result = plan(golden_5(), quota_history=history)
    assert result.item("gym").promoted


def test_e6_yoga_does_not_fit() -> None:
    result = plan(golden_5(), sleep_time="20:40")
    yoga = next(u for u in result.unslotted if u.key == "yoga")
    assert yoga.message == "Couldn't fit: Yoga (20m)"
    assert yoga.recommendations == ["Skip Yoga", "Shorten Gym", "Swap Yoga with Gym"]
    assert result.item("yoga") is None


def test_e8_gym_vs_class_plus_commute() -> None:
    data = golden_5()
    data["rules"] = [r for r in data["rules"] if r["id"] != "gym"]
    data["rules"].append(
        {"type": "event", "id": "gym", "label": "Gym", "category": "quota",
         "anchor": absolute("18:00"), "duration_minutes": 60, "days_of_week": [0]}
    )  # fmt: skip
    result = plan(data)
    conflict = next(c for c in result.conflicts if c.kind == "indirect")
    assert "Gym at 18:00 can never fit" in conflict.message
    assert "35m commute" in conflict.message
    assert conflict.options
    assert at(result, "gym") == "19:30"
    assert result.item("gym").status == "proposed"


def test_e11_day_override_removes_afternoon_class_for_that_date() -> None:
    data = golden_5()
    changed = plan(data, day_overrides={"no_commitments_after": "15:00"})
    assert changed.item("commitment:class_pm") is None
    assert changed.item("gym").start < 19 * 60
    other_day = plan(data)
    assert at(other_day, "gym") == "19:30"
    assert other_day.item("commitment:class_pm") is not None


def test_e12_item_cannot_be_slotted() -> None:
    rules = [
        {"type": "event", "id": "assignment", "label": "Assignment", "category": "task",
         "duration_minutes": 60, "before_first_commitment": True},
    ]  # fmt: skip
    commitment = {"id": "work", "label": "Work", "days_of_week": [0], "start": "08:30",
                  "end": "20:00"}  # fmt: skip
    result = plan({"date": MONDAY, "wake": "08:00", "rules": rules, "commitments": [commitment]})
    assert result.unslotted[0].message == "Couldn't fit: Assignment (1h)"
    assert result.unslotted[0].recommendations


def test_e13_lunch_dragged_after_gym() -> None:
    data = golden_5()
    result = plan(data, day_overrides={"reorder": [{"item_key": "lunch", "after": "gym"}]})
    gym, lunch, dose = result.item("gym"), result.item("lunch"), result.item("medicine_2")
    assert lunch.start == gym.end
    assert dose.start >= lunch.end
    assert dose.start - result.item("medicine_3").start >= 240
    assert any("Reorder breaks medicine spacing" in w for w in result.warnings)


def test_e13_no_warning_when_spacing_holds() -> None:
    rules = [dict(r) for r in golden_5()["rules"]]
    for rule in rules:
        rule.pop("min_gap_minutes", None)
    result = plan(golden_5(), rules=rules, commitments=[],
                  day_overrides={"reorder": [{"item_key": "lunch", "after": "gym"}]})  # fmt: skip
    assert result.item("medicine_2").start == result.item("lunch").end
    assert not [w for w in result.warnings if "spacing" in w]


def test_e14_wake_at_eleven_proposes_merging_meals() -> None:
    data = with_rules(MEDS_ROUTINE, lambda rule: _absolute_lunch(rule))
    result = plan(data, wake="11:00")
    merge = next(p for p in result.pending_approvals if p.kind == "meal_merge")
    assert merge.chosen.id == "merge"
    assert result.item("lunch") is None
    assert "merged" in result.item("breakfast").label
    doses = sorted(result.item(k).start for k in ("medicine_1", "medicine_2", "medicine_3"))
    assert doses[1] - doses[0] >= 240
    assert doses[2] - doses[1] >= 240
    assert at(result, "medicine_2") == "16:30"


def _absolute_lunch(rule: dict) -> dict:
    if rule["id"] == "lunch":
        return {**rule, "anchor": absolute("13:00"), "offset_minutes": 0}
    return rule


def test_e14_merge_is_not_proposed_on_a_normal_morning() -> None:
    data = with_rules(MEDS_ROUTINE, _absolute_lunch)
    assert not [p for p in plan(data, wake="07:00").proposals if p.kind == "meal_merge"]


def test_e15_gym_would_end_after_sleep() -> None:
    result = plan(golden_5(), sleep_time="20:00")
    gym = result.item("gym")
    assert "ends_after_sleep" in gym.flags
    proposal = next(p for p in result.pending_approvals if p.kind == "sleep")
    options = {proposal.chosen.id, *[o.id for o in proposal.alternatives]}
    assert {"shorten", "use_quota_miss"} <= options


def test_c1_calendar_lunch_replaces_rule_lunch() -> None:
    data = {**golden_5(), "commitments": [], **calendar(
        ("alex", "Lunch with Alex", "12:30", "13:30", "meal", "lunch"))}  # fmt: skip
    result = plan(data)
    assert result.item("lunch") is None
    assert result.item("cal:alex").label == "Lunch with Alex"
    assert at(result, "medicine_2") == "13:30"


def test_c2_meeting_during_meds() -> None:
    rules = [{"type": "event", "id": "pills", "label": "Pills", "category": "medicine",
              "anchor": absolute("14:30"), "allowed_during_busy": True}]  # fmt: skip
    data = {"date": MONDAY, "wake": "07:00", "rules": rules,
            **calendar(("m", "Review", "14:00", "15:00", "meeting", None))}  # fmt: skip
    proposal = plan(data).pending_approvals[0]
    assert proposal.kind == "during_busy"
    assert {proposal.chosen.id, proposal.alternatives[0].id} == {"during", "after"}


def test_c3_meeting_blocks_lunch() -> None:
    data = golden_6()
    data["calendar_events"][0].update(start="2026-11-02T12:00:00", end="2026-11-02T14:00:00")
    proposal = plan(data).pending_approvals[0]
    assert proposal.kind == "fallback"
    assert proposal.chosen.id == "food:apple:during_busy"
    assert len(proposal.alternatives) == 2


def test_c4_duplicate_class_is_flagged_and_counted_once() -> None:
    data = {**golden_5(), **calendar(
        ("dup", "CSE Class", "10:00", "14:00", "meeting", None))}  # fmt: skip
    result = plan(data)
    assert any(c.kind == "duplicate" for c in result.conflicts)
    assert result.item("cal:dup") is None
    assert result.item("commitment:class_am") is not None


def test_c8_location_drives_leave_by() -> None:
    with_place = {"date": MONDAY, "wake": "07:00", **calendar(
        ("a", "Client visit", "10:00", "11:00", "meeting", None, "Office Park"))}  # fmt: skip
    result = plan(with_place)
    assert at(result, "cal:a:leave") == "09:30"
    assert result.item("cal:a").flags == []
    without = {"date": MONDAY, "wake": "07:00", **calendar(
        ("b", "Client visit", "10:00", "11:00", "meeting", None))}  # fmt: skip
    result = plan(without)
    assert result.item("cal:b:leave") is None
    assert result.item("cal:b").flags == ["assumed_away"]


def test_c9_all_day_events_do_not_block_time() -> None:
    data = golden_5()
    data["calendar_events"] = [
        {"source_id": "bday", "title": "Mom's birthday", "start": "2026-11-02T00:00:00",
         "end": "2026-11-03T00:00:00", "all_day": True, "classified_as": "ignore"},
    ]  # fmt: skip
    result = plan(data)
    assert result.surfaced_events == ["Mom's birthday"]
    assert result.item("cal:bday") is None
    assert at(result, "breakfast") == "09:00"


def test_m1_restricted_keeps_c_pending_with_a_and_b() -> None:
    result = plan(golden_6())
    proposal = result.pending_approvals[0]
    assert proposal.chosen.id == "food:apple:during_busy"
    assert len(proposal.alternatives) == 2
    assert not result.auto_applied
    assert result.item("medicine_2").status == "proposed"


def test_m4_unrestricted_yoga_stays_unslotted() -> None:
    result = plan(golden_5(), sleep_time="20:40", approval_mode="unrestricted")
    assert any(u.key == "yoga" for u in result.unslotted)
    assert result.item("yoga") is None


def test_m5_unrestricted_cramming_keeps_spacing() -> None:
    data = {
        **MEDS_ROUTINE,
        "wake": "08:00",
        "approval_mode": "unrestricted",
        "day_overrides": {"late_mode": "continue", "restart_at": "12:30"},
        "item_status": {"breakfast": "done", "medicine_1": "not_done"},
    }
    result = plan(data)
    first, second = result.item("medicine_1"), result.item("medicine_2")
    assert first.start == 12 * 60 + 30
    assert second.start - first.start >= 240
    assert second.status == "auto"


def test_m8_standing_approval_auto_applies_only_its_case() -> None:
    rules = [
        {"type": "event", "id": "pills", "label": "Pills", "category": "medicine",
         "anchor": absolute("14:30"), "allowed_during_busy": True},
        {"type": "event", "id": "reading", "label": "Reading", "category": "routine",
         "anchor": absolute("14:15"), "duration_minutes": 30},
    ]  # fmt: skip
    standing = [{"match": {"kind": "during_busy", "category": "medicine", "block_kind": "meeting"},
                 "action": {"choice": "during"}}]  # fmt: skip
    data = {"date": MONDAY, "wake": "07:00", "rules": rules, "standing_approvals": standing,
            **calendar(("m", "Review", "14:00", "15:00", "meeting", None))}  # fmt: skip
    result = plan(data)
    pills = result.item("pills")
    assert pills.start == 14 * 60 + 30
    assert pills.silent
    assert pills.status == "scheduled"
    assert [p.key for p in result.pending_approvals] == ["move:reading"]
