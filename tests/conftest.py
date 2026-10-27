"""Force offline modes before anything reads settings, whatever .env says.

Live tests run only when the real environment sets CALENDAR_MODE=mcp. Only that variable counts,
not .env, so a normal `pytest` run never reaches Google.
"""

import os

LIVE_CALENDAR = os.environ.get("CALENDAR_MODE") == "mcp"

os.environ["LLM_MODE"] = "mock"
os.environ["CALENDAR_MODE"] = "mock"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
