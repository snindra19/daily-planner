"""Live and mock LLM clients with one interface, plus validated structured calls."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import ROOT, Settings, get_settings

log = logging.getLogger("planner.llm")
RECORDINGS = ROOT / "fixtures" / "llm_recordings"
PROMPTS = Path(__file__).resolve().parent / "prompts"
MAX_TOKENS = 16000
Output = TypeVar("Output", bound=BaseModel)


class LLMError(Exception):
    """An LLM task failed. The message is safe to show to the user."""


class CouldNotUnderstand(LLMError):
    """The output stayed invalid after one retry."""

    def __init__(self) -> None:
        super().__init__("Couldn't understand this. Try rewording it.")


class RecordingMissing(LLMError):
    """Mock mode has no recording for these inputs."""


def input_hash(task: str, primary: Any) -> str:
    """Hash the inputs that identify a recording."""
    canonical = json.dumps({"task": task, "primary": primary}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_prompt(task: str) -> str:
    """Return a task's system prompt."""
    return (PROMPTS / f"{task}.md").read_text(encoding="utf-8")


class LLMClient(Protocol):
    """Anything that can answer a structured task."""

    def raw(self, task: str, schema: dict[str, Any], primary: Any, context: Any) -> dict[str, Any]:
        """Return the unvalidated tool input for a task."""
        ...


class MockClient:
    """Returns recorded outputs from fixtures/llm_recordings, keyed by a hash of the inputs."""

    def __init__(self, directory: Path = RECORDINGS) -> None:
        self.recordings: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.recordings[input_hash(data["task"], data["primary"])] = data["output"]

    def raw(self, task: str, schema: dict[str, Any], primary: Any, context: Any) -> dict[str, Any]:
        """Return the recording for these inputs, or raise RecordingMissing."""
        normalized = json.loads(json.dumps(primary, default=str))
        output = self.recordings.get(input_hash(task, normalized))
        if output is None:
            raise RecordingMissing(f"No mock recording for this {task} input.")
        return output


class LiveClient:
    """Calls Claude through the Anthropic SDK and logs token counts, never the key."""

    def __init__(self, settings: Settings) -> None:
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            raise LLMError("LLM_MODE=live needs ANTHROPIC_API_KEY.")
        self.client = anthropic.Anthropic(api_key=key, timeout=120.0)
        self.model = settings.anthropic_model

    def raw(self, task: str, schema: dict[str, Any], primary: Any, context: Any) -> dict[str, Any]:
        """Ask for one tool call. tool_choice stays auto because this model rejects forcing it."""
        tool_name = f"submit_{task}"
        system = (
            f"{load_prompt(task)}\n\nReturn your answer only by calling the {tool_name} tool, "
            "exactly once."
        )
        content = json.dumps({"input": primary, "context": context}, indent=1, default=str)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[
                    {
                        "name": tool_name,
                        "description": f"Submit the structured result of {task}.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.APIConnectionError as error:
            raise LLMError("Could not reach the LLM. Check your connection.") from error
        except anthropic.AuthenticationError as error:
            raise LLMError("The Anthropic API key was rejected.") from error
        except anthropic.RateLimitError as error:
            raise LLMError("The LLM is rate limited. Try again shortly.") from error
        except anthropic.APIStatusError as error:
            raise LLMError(f"The LLM returned an error ({error.status_code}).") from error
        log.info(
            "llm task=%s model=%s input_tokens=%s output_tokens=%s stop=%s",
            task, self.model, response.usage.input_tokens, response.usage.output_tokens,
            response.stop_reason,
        )  # fmt: skip
        if response.stop_reason == "refusal":
            raise LLMError("The LLM declined this request.")
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {}


def make_client(settings: Settings | None = None) -> LLMClient:
    """Build the client for the configured mode."""
    current = settings or get_settings()
    return LiveClient(current) if current.llm_mode == "live" else MockClient()


def call_structured(
    client: LLMClient, task: str, model: type[Output], primary: Any, context: Any = None
) -> Output:
    """Run a task and validate the output. Retries once, then raises CouldNotUnderstand."""
    schema = model.model_json_schema()
    for _ in range(2):
        data = client.raw(task, schema, primary, context)
        try:
            return model.model_validate(data)
        except ValidationError as error:
            log.warning("llm task=%s invalid output: %s errors", task, error.error_count())
    raise CouldNotUnderstand()
