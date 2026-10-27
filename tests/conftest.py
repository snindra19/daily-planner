"""Force offline modes before anything reads settings, whatever .env says.

Live tests run only when the real environment sets CALENDAR_MODE=mcp or LLM_MODE=live. Values in
.env do not count, so a normal `pytest` run never reaches Google or the Anthropic API.
"""

import os

LIVE_CALENDAR = os.environ.get("CALENDAR_MODE") == "mcp"
LIVE_LLM = os.environ.get("LLM_MODE") == "live"

os.environ["LLM_MODE"] = "mock"
os.environ["CALENDAR_MODE"] = "mock"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
if not LIVE_LLM:
    os.environ["ANTHROPIC_API_KEY"] = ""
