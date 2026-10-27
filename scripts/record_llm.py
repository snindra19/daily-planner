"""Record a live LLM output into fixtures/llm_recordings. Needs LLM_MODE=live and a real key.

Example:
    uv run python scripts/record_llm.py parse my-routine '{"text": "wake up at 7"}'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.llm.client import LiveClient, call_structured  # noqa: E402
from app.schemas import llm as outputs  # noqa: E402

MODELS = {
    "parse": outputs.ParseResult,
    "questions": outputs.QuestionsResult,
    "contradictions": outputs.ContradictionsResult,
    "prep": outputs.PrepResult,
    "medicine": outputs.MedicineSuggestion,
    "classify": outputs.ClassificationResult,
    "explain": outputs.ExplanationResult,
    "analyze": outputs.PatternsResult,
}


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in MODELS:
        sys.exit(f"Usage: record_llm.py <{'|'.join(MODELS)}> <name> '<input json>'")
    task, name, raw = sys.argv[1:]
    primary = json.loads(raw)
    result = call_structured(LiveClient(get_settings()), task, MODELS[task], primary)
    record = {"task": task, "primary": primary, "output": result.model_dump(exclude_none=True)}
    path = ROOT / "fixtures" / "llm_recordings" / f"{name}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {path}")


if __name__ == "__main__":
    main()
