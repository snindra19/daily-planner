"""Application settings, read from the environment and .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Real environment variables win over .env."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_secret_key: SecretStr = SecretStr("")
    app_timezone: str = "America/Bogota"
    database_url: str = f"sqlite:///{ROOT / 'planner.db'}"

    llm_mode: str = "mock"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-sonnet-5-5"

    calendar_mode: str = "mock"
    mock_calendar_path: str = str(ROOT / "fixtures" / "mock_calendar.json")
    mcp_calendar_command: str = ""
    mcp_calendar_args: str = ""
    mcp_calendar_env_file: str = ""
    mcp_list_events_tool: str = ""
    mcp_calendar_id: str = "primary"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings."""
    return Settings()
