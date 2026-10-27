"""Section 16 scenarios that need the database, recorded LLM outputs, or the mock calendar."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.calendar.classify import normalize_title
from app.calendar.mock import StaticCalendar
from app.llm.client import LLMError, MockClient
from app.models import (
    ActivityLog,
    Approval,
    DecisionHistory,
    LearnedPreference,
    Question,
    Rule,
    StandingApprovalRow,
    TitleClassification,
)
from app.schemas.scheduling import CalendarEvent
from app.services import (
    approval_service,
    learning_service,
    plan_service,
    prep_service,
    rule_service,
)
from app.services.profile_service import get_profile, update_changes
from tests.helpers import at, load
from tests.scenarios import cases
from tests.scenarios.conftest import seed

MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
ANALYSIS = date.fromisoformat(cases.ANALYSIS_DAY)
llm = MockClient()


def event(
    source_id: str, title: str, start: str, end: str, day: str = "2026-11-02", **fields
) -> CalendarEvent:
    return CalendarEvent(
        source_id=source_id,
        title=title,
        start=datetime.fromisoformat(f"{day}T{start}:00"),
        end=datetime.fromisoformat(f"{day}T{end}:00"),
        **fields,
    )


def fixture_events(data: dict) -> StaticCalendar:
    return StaticCalendar([CalendarEvent(**row) for row in data.get("calendar_events", [])])


def confirm_all(db: Session, drafts: list[Rule]) -> None:
    for rule in drafts:
        rule_service.confirm_rule(db, rule.id, llm)


def history(db: Session, rows: list[dict]) -> None:
    for row in rows:
        db.add(DecisionHistory(**row))
    db.commit()


# Positive workflows


def test_p1_first_setup(db: Session) -> None:
    get_profile(db)
    drafts = rule_service.parse_text(db, cases.P1_TEXT, llm)
    assert len(drafts) == 9
    assert all(r.status == "draft" for r in drafts)
    assert db.query(Rule).filter_by(status="confirmed").count() == 0

    questions = [q.text for q in db.query(Question).filter_by(status="pending").all()]
    assert any("Which meals" in q for q in questions)
    assert any("How long is a gym" in q for q in questions)
    assert any("Where do you go to the gym" in q for q in questions)

    meds = next(r for r in drafts if r.payload.get("id") == "meds")
    assert "after breakfast and after dinner" in meds.suggestion["summary"]

    confirm_all(db, drafts)
    assert plan_service.compute_plan(db, MONDAY).result.needs_confirmation == ["Meds"]

    rule_service.accept_suggestion(db, meds.id)
    result = plan_service.compute_plan(db, MONDAY).result
    assert result.needs_confirmation == []
    assert at(result, "breakfast") == "08:00"
    assert at(result, "meds_1") == "08:20"
    assert result.item("meds_2").start == result.item("dinner").end
    assert result.item("gym") is not None
    assert get_profile(db).default_wake_by_weekday["5"] == "09:00"


def test_p1_changing_an_answer_makes_dependents_obsolete(db: Session) -> None:
    rule_service.parse_text(db, cases.P1_TEXT, llm)
    length = db.query(Question).filter_by(question_key="gym_length").one()
    location = db.query(Question).filter_by(question_key="gym_location").one()
    rule_service.answer_question(db, length.id, "1 hour", None)
    rule_service.answer_question(db, location.id, "At home", None)
    assert location.status == "answered"
    rule_service.answer_question(db, length.id, "45 minutes", None)
    assert location.status == "obsolete"


def test_p2_night_review(db: Session) -> None:
    seed(db, load("golden_16_5"))
    drafts = rule_service.parse_text(db, cases.P2_TEXT, llm, only_date=MONDAY.isoformat())
    assert plan_service.compute_plan(db, MONDAY).result.item("submit_assignment") is None
    confirm_all(db, drafts)
    result = plan_service.compute_plan(db, MONDAY).result
    assignment = result.item("submit_assignment")
    assert assignment.end <= result.item("commitment:1:leave").start
    assert plan_service.compute_plan(db, TUESDAY).result.item("submit_assignment") is None


def p3_data() -> dict:
    data = load("golden_16_5")
    data["commitments"] = []
    rules = [r for r in data["rules"] if r["id"] not in ("gym", "yoga")]
    data["rules"] = rules
    return data


def p3_calendar() -> StaticCalendar:
    return StaticCalendar([CalendarEvent(**e) for e in cases.P3_EVENTS])


def test_p3_calendar_dinner_with_prep_approvals(db: Session) -> None:
    seed(db, p3_data())
    source = p3_calendar()
    view = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source)
    assert view.result.item("dinner") is None
    assert view.result.item("cal:dinner_friends").label == "Dinner with friends"
    assert view.result.item("medicine_3").start == 21 * 60

    prep_service.run_prep(db, TUESDAY, llm, view.result, view.mode)
    pending = db.query(Approval).filter_by(kind="prep_reminder", status="pending").all()
    assert sorted(p.summary for p in pending) == ["Add Pack medicines", "Add Take laptop to dinner"]

    pack = next(p for p in pending if "Pack" in p.summary)
    approval_service.decide(db, pack.id, "approve")
    result = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source).result
    leave = result.item("cal:dinner_friends:leave")
    assert result.item("prep_pack_medicines").start == leave.start - 30


def test_p4_actual_equals_target_changes_nothing(db: Session) -> None:
    seed(db, load("golden_16_4"))
    row = plan_service.get_day_plan(db, MONDAY.isoformat())
    row.target_wake = "08:00"
    db.commit()
    plan_service.compute_plan(db, MONDAY)
    row.actual_wake = "08:00"
    db.commit()
    assert plan_service.compute_plan(db, MONDAY).diff == []


def test_p5_earlier_wake_keeps_fixed_blocks(db: Session) -> None:
    seed(db, load("golden_16_5"))
    row = plan_service.get_day_plan(db, MONDAY.isoformat())
    row.target_wake = "08:00"
    db.commit()
    before = plan_service.compute_plan(db, MONDAY).result
    row.actual_wake = "07:30"
    db.commit()
    view = plan_service.compute_plan(db, MONDAY)
    assert view.wake_level == "actual"
    assert at(view.result, "breakfast") == "08:30"
    assert before.item("commitment:1").start == view.result.item("commitment:1").start
    assert before.item("commitment:2").start == view.result.item("commitment:2").start
    assert any(c["key"] == "breakfast" for c in view.diff)


# Edge cases


def test_e7_contradiction_is_shown_and_the_user_picks(db: Session) -> None:
    old = Rule(raw_text="dinner done by 8", payload=cases.DINNER_BY_8, kind="constraint",
               status="confirmed")  # fmt: skip
    db.add(old)
    db.commit()
    drafts = rule_service.parse_text(db, cases.E7_TEXT, llm)
    new = drafts[0]
    found = rule_service.confirm_rule(db, new.id, llm)
    assert len(found) == 1
    assert "dinner at 9 PM" in found[0].summary and "dinner done by 8" in found[0].summary
    assert new.status == "draft"
    assert old.status == "confirmed"

    get_profile(db).approval_mode = "unrestricted"
    plan_service.compute_plan(db, MONDAY)
    assert found[0].status == "pending"

    approval_service.decide(db, found[0].id, "pick", "keep_old")
    assert new.status == "archived"
    assert old.status == "confirmed"


def test_e7_user_can_keep_the_new_rule(db: Session) -> None:
    old = Rule(raw_text="dinner done by 8", payload=cases.DINNER_BY_8, kind="constraint",
               status="confirmed")  # fmt: skip
    db.add(old)
    db.commit()
    new = rule_service.parse_text(db, cases.E7_TEXT, llm)[0]
    found = rule_service.confirm_rule(db, new.id, llm)
    approval_service.decide(db, found[0].id, "pick", "keep_new")
    assert new.status == "confirmed"
    assert old.status == "archived"


def test_e8_conflict_is_found_on_the_profile_page_data(db: Session) -> None:
    data = load("golden_16_5")
    data["rules"] = [
        {"type": "event", "id": "gym", "label": "Gym", "category": "quota",
         "anchor": {"ref": "absolute", "time": "18:00"}, "duration_minutes": 60,
         "days_of_week": [0]},
    ]  # fmt: skip
    seed(db, data)
    conflicts = rule_service.indirect_conflicts(db)
    assert len(conflicts) == 1
    assert "Gym at 18:00" in conflicts[0].message


def test_e9_and_m6_medicine_without_timing(db: Session) -> None:
    seed(db, load("golden_16_4"), mode="unrestricted")
    drafts = rule_service.parse_text(db, cases.E9_TEXT, llm)
    vitamin = drafts[0]
    assert vitamin.suggestion["summary"].startswith("Suggestion:")
    confirm_all(db, drafts)
    result = plan_service.compute_plan(db, MONDAY).result
    assert result.item("vitamin_d") is None
    assert result.needs_confirmation == ["Vitamin D"]
    assert not any("vitamin" in p.key for p in result.proposals)
    rule_service.accept_suggestion(db, vitamin.id)
    result = plan_service.compute_plan(db, MONDAY).result
    assert result.item("vitamin_d_1").start == result.item("breakfast").end


def test_e10_skipped_night_review_uses_the_default(db: Session) -> None:
    seed(db, load("golden_16_4"))
    get_profile(db).default_wake_by_weekday = {"0": "06:00"}
    db.commit()
    view = plan_service.compute_plan(db, MONDAY)
    assert (view.wake, view.wake_level) == ("06:00", "default")
    assert at(view.result, "breakfast") == "07:00"


def test_e11_parsed_day_override_applies_to_that_date_only(db: Session) -> None:
    seed(db, load("golden_16_5"))
    baseline = plan_service.compute_plan(db, MONDAY).result
    assert at(baseline, "gym") == "19:30"
    next_monday = date(2026, 11, 9)
    drafts = rule_service.parse_text(db, cases.E11_TEXT, llm, only_date=MONDAY.isoformat())
    confirm_all(db, drafts)
    changed = plan_service.compute_plan(db, MONDAY).result
    assert changed.item("commitment:2") is None
    assert changed.item("gym").start < 19 * 60
    assert changed.item("yoga").start < baseline.item("yoga").start
    other = plan_service.compute_plan(db, next_monday).result
    assert other.item("commitment:2") is not None
    assert at(other, "gym") == "19:30"


def test_e5_skip_needs_a_second_answer_over_the_route_data(db: Session) -> None:
    data = load("golden_16_5")
    seed(db, data)
    rows = [{"date": "2026-11-01", "item_key": "gym", "status": "not_done"}]
    from app.models import ItemStatus

    db.add(ItemStatus(**rows[0]))
    db.commit()
    result = plan_service.compute_plan(db, MONDAY).result
    assert result.skip_warnings["gym"] == "Gym miss limit reached this month. Skip anyway?"
    assert result.item("gym") is not None


# Calendar cases


def test_c5_new_meeting_after_review_shows_in_the_diff(db: Session) -> None:
    seed(db, load("golden_16_4"))
    plan_service.compute_plan(db, MONDAY, source=StaticCalendar())
    source = StaticCalendar([event("new", "Standup", "09:00", "09:30")])
    view = plan_service.compute_plan(db, MONDAY, source=source)
    assert any(c["change"] == "added" and "Standup" in c["text"] for c in view.diff)


def test_c6_cancelled_event_frees_time_and_removes_its_approvals(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data)
    view = plan_service.compute_plan(db, MONDAY, source=fixture_events(data))
    assert db.query(Approval).filter_by(status="pending").count() == 1
    assert at(view.result, "medicine_2") == "12:30"
    view = plan_service.compute_plan(db, MONDAY, source=StaticCalendar())
    assert db.query(Approval).filter_by(status="pending").count() == 0
    assert at(view.result, "lunch") == "12:00"


def test_c6_cancelled_event_removes_its_prep_reminders(db: Session) -> None:
    seed(db, p3_data())
    source = p3_calendar()
    view = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source)
    prep_service.run_prep(db, TUESDAY, llm, view.result, view.mode)
    pending = db.query(Approval).filter_by(kind="prep_reminder", status="pending").all()
    approval_service.decide(db, pending[0].id, "approve")
    result = plan_service.compute_plan(db, TUESDAY, llm=llm, source=StaticCalendar()).result
    assert db.query(Approval).filter_by(kind="prep_reminder", status="pending").count() == 0
    assert result.item("prep_pack_medicines") is None
    assert not result.warnings


def test_c7_unclear_title_is_classified_confirmed_once_and_remembered(db: Session) -> None:
    seed(db, load("golden_16_5"))
    day = "2026-11-02"
    source = StaticCalendar([event("c", "Catch-up at Chipotle", "12:30", "13:30", day)])
    view = plan_service.compute_plan(db, MONDAY, llm=llm, source=source)
    assert view.result.item("cal:c").category == "meal"
    question = db.query(Approval).filter_by(kind="classification", status="pending").one()
    approval_service.decide(db, question.id, "approve")
    saved = db.get(TitleClassification, normalize_title("Catch-up at Chipotle"))
    assert saved.classified_as == "meal"

    class NoClassify:
        def raw(self, task, *args):
            assert task != "classify", "A remembered title must not call the LLM."
            raise LLMError("Not recorded.")

    later = StaticCalendar([event("d", "catch-up at  chipotle", "12:30", "13:30", "2026-11-09")])
    result = plan_service.compute_plan(db, date(2026, 11, 9), llm=NoClassify(), source=later).result
    assert result.item("cal:d").category == "meal"
    assert db.query(Approval).filter_by(kind="classification", status="pending").count() == 0


def test_c10_sync_failure_shows_status_and_plans_from_rules(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data)
    plan_service.compute_plan(db, MONDAY, source=fixture_events(data))
    view = plan_service.compute_plan(db, MONDAY, source=StaticCalendar(error="auth expired"))
    assert not view.calendar.ok
    assert view.calendar.message == "auth expired"
    assert view.result.item("cal:meeting") is None
    assert at(view.result, "lunch") == "12:00"


# Approval modes


def test_m2_unrestricted_auto_applies_logs_and_undo_restores(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data, mode="unrestricted")
    source = fixture_events(data)
    view = plan_service.compute_plan(db, MONDAY, source=source)
    assert view.result.item("medicine_2").status == "auto"
    assert not view.result.pending_approvals
    entry = db.query(ActivityLog).one()
    assert entry.change["option"] == "food:apple:during_busy"
    assert len(entry.alternatives) == 2

    approval_service.undo(db, entry.id)
    undone = plan_service.compute_plan(db, MONDAY, source=source)
    assert not undone.result.auto_applied
    assert undone.result.pending_approvals[0].key == "fallback:lunch"
    assert db.query(ActivityLog).count() == 1

    signal = db.query(DecisionHistory).filter_by(signal="undone").one()
    assert signal.chosen == "food:apple:during_busy"

    update_changes(db, MONDAY.isoformat(), no_commitments_after="23:00")
    changed = plan_service.compute_plan(db, MONDAY, source=source)
    assert [p.key for p in changed.result.auto_applied] == ["fallback:lunch"]


def test_m3_unrestricted_prep_is_added_automatically(db: Session) -> None:
    seed(db, p3_data(), mode="unrestricted")
    source = p3_calendar()
    view = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source)
    prep_service.run_prep(db, TUESDAY, llm, view.result, view.mode)
    result = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source).result
    assert result.item("prep_pack_medicines") is not None
    assert result.item("prep_take_laptop_to_dinner") is not None
    assert db.query(Approval).filter_by(kind="prep_reminder").count() == 0
    logged = [e for e in db.query(ActivityLog).all() if e.change.get("kind") == "prep_reminder"]
    assert {e.applied_by for e in logged} == {"auto"}
    assert len(logged) == 2

    approval_service.undo(db, logged[0].id)
    result = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source).result
    assert result.item(logged[0].change["item_key"]) is None


def test_m7_restricted_just_for_tomorrow(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data, mode="unrestricted")
    tomorrow_events = [
        CalendarEvent(**{**e, "start": e["start"].replace("11-02", "11-03"),
                         "end": e["end"].replace("11-02", "11-03")})
        for e in data["calendar_events"]
    ]  # fmt: skip
    update_changes(db, TUESDAY.isoformat(), approval_mode="restricted")
    tomorrow = plan_service.compute_plan(db, TUESDAY, source=StaticCalendar(tomorrow_events))
    assert tomorrow.mode == "restricted"
    assert tomorrow.result.pending_approvals and not tomorrow.result.auto_applied
    today = plan_service.compute_plan(db, MONDAY, source=fixture_events(data))
    assert today.mode == "unrestricted"
    assert today.result.auto_applied and not today.result.pending_approvals


def test_m8_always_creates_a_standing_approval(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data)
    source = fixture_events(data)
    plan_service.compute_plan(db, MONDAY, source=source)
    row = db.query(Approval).filter_by(status="pending").one()
    approval_service.decide(db, row.id, "always")
    assert db.query(StandingApprovalRow).count() == 1
    next_week = [
        CalendarEvent(**{**e, "start": e["start"].replace("11-02", "11-09"),
                         "end": e["end"].replace("11-02", "11-09")})
        for e in data["calendar_events"]
    ]  # fmt: skip
    result = plan_service.compute_plan(
        db, date(2026, 11, 9), source=StaticCalendar(next_week)
    ).result
    assert not result.pending_approvals
    assert result.item("medicine_2").start == 12 * 60 + 30


# Learning


def test_l1_pattern_raises_the_weight_and_16_6_auto_applies_after_the_meeting(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data, mode="unrestricted")
    history(db, cases.L1_HISTORY)
    assert learning_service.analyze(db, ANALYSIS, llm) is None
    pref = db.query(LearnedPreference).one()
    assert pref.status == "active"
    assert pref.evidence_count == 7
    assert round(pref.consistency * 7) == 6
    result = plan_service.compute_plan(db, MONDAY, source=fixture_events(data)).result
    assert result.auto_applied[0].chosen.id == "meal_nearest_slot"
    assert at(result, "lunch") == "14:00"
    assert at(result, "medicine_2") == "14:30"


def test_l2_two_decisions_are_below_the_threshold(db: Session) -> None:
    seed(db, load("golden_16_6"), mode="unrestricted")
    history(db, cases.L2_HISTORY)
    learning_service.analyze(db, ANALYSIS, llm)
    assert db.query(LearnedPreference).count() == 0


def test_l3_rejected_prep_stops_being_proposed(db: Session) -> None:
    seed(db, p3_data(), mode="unrestricted")
    history(db, cases.L3_HISTORY)
    learning_service.analyze(db, ANALYSIS, llm)
    pref = db.query(LearnedPreference).one()
    assert pref.status == "active"
    get_profile(db).approval_mode = "restricted"
    db.commit()
    source = p3_calendar()
    view = plan_service.compute_plan(db, TUESDAY, llm=llm, source=source)
    prep_service.run_prep(db, TUESDAY, llm, view.result, view.mode)
    labels = [a.summary for a in db.query(Approval).filter_by(kind="prep_reminder").all()]
    assert labels == ["Add Pack medicines"]
    learning_service.set_status(db, pref.id, "revoke")
    assert pref.status == "revoked"


def test_l4_restricted_pattern_waits_to_be_accepted(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data)
    history(db, cases.L1_HISTORY)
    learning_service.analyze(db, ANALYSIS, llm)
    pref = db.query(LearnedPreference).one()
    assert pref.status == "proposed"
    source = fixture_events(data)
    result = plan_service.compute_plan(db, MONDAY, source=source).result
    assert result.pending_approvals[0].chosen.id == "food:apple:during_busy"
    learning_service.set_status(db, pref.id, "accept")
    result = plan_service.compute_plan(db, MONDAY, source=source).result
    assert result.pending_approvals[0].chosen.id == "meal_nearest_slot"


def test_l5_skip_yoga_is_recommended_first_and_never_automatic(db: Session) -> None:
    data = load("golden_16_5")
    data["sleep_time"] = "20:40"
    data["rules"] = [{**r, "id": "yoga"} if r["id"] == "yoga" else r for r in data["rules"]]
    seed(db, data, mode="unrestricted")
    history(db, cases.L5_HISTORY)
    learning_service.analyze(db, ANALYSIS, llm)
    assert db.query(LearnedPreference).one().status == "active"
    get_profile(db).sleep_time = "20:40"
    db.commit()
    result = plan_service.compute_plan(db, MONDAY).result
    yoga = next(u for u in result.unslotted if u.key == "yoga")
    assert yoga.recommendations[0] == "Skip Yoga"
    assert plan_service.compute_plan(db, MONDAY).result.item("yoga") is None
    from app.models import ItemStatus

    assert db.query(ItemStatus).filter_by(item_key="yoga").count() == 0


def _active_pattern(db: Session) -> LearnedPreference:
    history(db, cases.L1_HISTORY)
    learning_service.analyze(db, ANALYSIS, llm)
    return db.query(LearnedPreference).one()


def _dilute(db: Session) -> None:
    """Add opposite decisions until consistency is 50%."""
    rows = [
        cases.decision(d, "fallback", cases.FALLBACK_CONTEXT, cases.APPLE_DURING, "approved")
        for d in range(8, 13)
    ]
    history(db, rows)


def test_l6_drift_in_unrestricted_revokes_with_a_log_entry(db: Session) -> None:
    seed(db, load("golden_16_6"), mode="unrestricted")
    pref = _active_pattern(db)
    _dilute(db)
    learning_service.recheck(db, ANALYSIS)
    db.commit()
    assert round(pref.consistency, 2) == 0.5
    assert pref.status == "revoked"
    assert any("Revoked" in e.change.get("label", "") for e in db.query(ActivityLog).all())


def test_l6_drift_in_restricted_goes_back_to_proposed(db: Session) -> None:
    seed(db, load("golden_16_6"))
    pref = _active_pattern(db)
    learning_service.set_status(db, pref.id, "accept")
    _dilute(db)
    learning_service.recheck(db, ANALYSIS)
    db.commit()
    assert pref.status == "proposed"


def test_l7_undo_is_a_negative_signal(db: Session) -> None:
    data = load("golden_16_6")
    seed(db, data, mode="unrestricted")
    plan_service.compute_plan(db, MONDAY, source=fixture_events(data))
    approval_service.undo(db, db.query(ActivityLog).one().id)
    row = db.query(DecisionHistory).one()
    assert row.signal == "undone"
    assert row.context["meal"] == "lunch"


def test_hard_rules_cannot_be_learned(db: Session) -> None:
    seed(db, load("golden_16_6"), mode="unrestricted")
    from app.schemas.llm import PatternCandidate

    bad = [
        {"type": "weight", "key": "min_gap_minutes", "value": 0},
        {"type": "auto_skip", "item": "yoga"},
    ]
    for effect in bad:
        candidate = PatternCandidate(description="x", kind="fallback", effect=effect)
        assert not learning_service._allowed(candidate)
