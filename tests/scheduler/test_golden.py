"""Golden tests 16.4 to 16.6."""

from tests.helpers import at, load, plan, rows


def test_16_4_original_routine() -> None:
    data = load("golden_16_4")
    assert rows(plan(data)) == data["expected_rows"]


def test_16_5_monday_with_classes() -> None:
    data = load("golden_16_5")
    result = plan(data)
    assert rows(result) == data["expected_rows"]
    assert result.item("gym").end == 20 * 60 + 30
    assert result.item("yoga").end == 20 * 60 + 50
    assert result.item("commitment:class_am").end == 14 * 60


def test_16_5_lunch_scores_match_the_spec() -> None:
    fallback = plan(load("golden_16_5")).proposals[0]
    assert fallback.chosen.id == "meal_nearest_slot"
    assert fallback.chosen.score == 60
    assert [o.score for o in fallback.alternatives] == [80]


def test_16_6_candidates_and_scores() -> None:
    result = plan(load("golden_16_6"))
    proposal = result.pending_approvals[0]
    assert proposal.kind == "fallback"
    assert proposal.chosen.id == "food:apple:during_busy"
    assert proposal.chosen.score == 80
    assert [(o.id, o.score) for o in proposal.alternatives] == [
        ("meal_nearest_slot", 120),
        ("food:snack", 125),
    ]
    assert proposal.chosen.meal_skipped == "Lunch skipped"
    assert at(result, "medicine_2") == "12:30"


def test_16_6_alternatives_have_the_spec_times() -> None:
    proposal = plan(load("golden_16_6")).pending_approvals[0]
    full_lunch, snack = proposal.alternatives
    assert full_lunch.placements["lunch"]["start"] == 14 * 60
    assert full_lunch.placements["medicine_2"]["start"] == 14 * 60 + 30
    assert snack.placements["lunch"]["start"] == 14 * 60
    assert snack.placements["medicine_2"]["start"] == 14 * 60 + 15


def test_16_6_high_during_busy_weight_makes_full_lunch_win() -> None:
    weights = {"per_minute_delay": 1, "during_busy": 150, "meal_downgraded": 20}
    result = plan(load("golden_16_6"), weights=weights)
    proposal = result.pending_approvals[0]
    assert proposal.chosen.id == "meal_nearest_slot"
    assert proposal.chosen.score == 120
    assert at(result, "lunch") == "14:00"
    assert at(result, "medicine_2") == "14:30"
