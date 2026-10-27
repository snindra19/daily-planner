"""Force offline modes before anything reads settings, whatever .env says."""

import os

os.environ["LLM_MODE"] = "mock"
os.environ["CALENDAR_MODE"] = "mock"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
