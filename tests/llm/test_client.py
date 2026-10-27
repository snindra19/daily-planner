"""Schema validation, retry on invalid output, and mock recordings round-trip."""

from __future__ import annotations

from typing import Any

import pytest

from app.llm import tasks
from app.llm.client import CouldNotUnderstand, MockClient, RecordingMissing, call_structured
from app.schemas.llm import ClassificationResult, ParseResult
from tests.scenarios import cases


class Scripted:
    """Returns queued outputs and counts calls."""

    def __init__(self, *outputs: dict[str, Any]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def raw(self, task: str, schema: dict, primary: Any, context: Any) -> dict[str, Any]:
        self.calls += 1
        return self.outputs.pop(0)


GOOD = {"classified_as": "meal", "meal_type": "lunch"}


def test_valid_output_needs_one_call() -> None:
    client = Scripted(GOOD)
    result = call_structured(client, "classify", ClassificationResult, {"title": "x"})
    assert result.classified_as == "meal"
    assert client.calls == 1


def test_invalid_output_is_retried_once() -> None:
    client = Scripted({"classified_as": "party"}, GOOD)
    assert call_structured(client, "classify", ClassificationResult, {}).meal_type == "lunch"
    assert client.calls == 2


def test_two_invalid_outputs_surface_could_not_understand() -> None:
    client = Scripted({"classified_as": "party"}, {})
    with pytest.raises(CouldNotUnderstand) as caught:
        call_structured(client, "classify", ClassificationResult, {})
    assert "Couldn't understand" in str(caught.value)
    assert client.calls == 2


def test_every_task_schema_can_be_a_tool_schema() -> None:
    schema = ParseResult.model_json_schema()
    assert schema["type"] == "object"
    assert "rules" in schema["properties"]


def test_mock_recordings_round_trip() -> None:
    client = MockClient()
    parsed = tasks.parse(client, cases.P1_TEXT, [])
    kinds = {r.payload.type for r in parsed.rules}
    assert {"event", "quota", "commitment", "profile_setting"} <= kinds


def test_mock_lookup_ignores_context_but_not_inputs() -> None:
    client = MockClient()
    with_rules = tasks.parse(client, cases.P2_TEXT, [{"rule_id": 1}])
    assert with_rules.rules[0].payload.label == "Submit assignment"
    with pytest.raises(RecordingMissing):
        tasks.parse(client, "something never recorded", [])


def test_no_recording_contains_a_final_time_for_a_medicine() -> None:
    """The LLM only gives relative prep times. Code computes the clock."""
    prep = tasks.prep(MockClient(), cases.P3_EVENTS, {})
    assert all(p.relative_to.startswith("cal:") for p in prep.proposals)
