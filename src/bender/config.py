"""Configuration module — loads and validates environment variables."""

import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Required: Slack tokens
    slack_bot_token: str
    slack_app_token: str

    # Required: Claude Code authentication (at least one)
    anthropic_api_key: str | None = None
    claude_code_oauth_token: str | None = None

    # Optional
    bender_workspace: Path = Path.cwd()
    bender_allowed_channels: str
    bender_timeout_seconds: int = Field(default=900, ge=1, le=3600)
    bender_permission_mode: (
        Literal[
            "acceptEdits",
            "auto",
            "bypassPermissions",
            "manual",
            "dontAsk",
            "plan",
        ]
        | None
    ) = None
    bender_api_host: str = "127.0.0.1"
    bender_api_port: int = 8080
    log_level: str = "info"

    # Optional: API key for authenticating external HTTP requests
    bender_api_key: str | None = None

    model_config = {"case_sensitive": False}

    @field_validator("bender_allowed_channels")
    @classmethod
    def validate_allowed_channels(cls, value: str) -> str:
        """Require an explicit comma-separated Slack channel allowlist."""
        entries = [entry.strip() for entry in value.split(",")]
        if not entries or any(
            not entry or re.fullmatch(r"[CG][A-Z0-9]+", entry) is None for entry in entries
        ):
            raise ValueError(
                "BENDER_ALLOWED_CHANNELS must contain comma-separated Slack channel IDs"
            )
        return ",".join(entries)

    @property
    def allowed_channel_ids(self) -> frozenset[str]:
        """Return the normalized Slack channel allowlist."""
        return frozenset(self.bender_allowed_channels.split(","))

    def validate_auth(self) -> None:
        """Ensure at least one Claude Code authentication method is configured."""
        if not self.anthropic_api_key and not self.claude_code_oauth_token:
            raise ValueError(
                "At least one authentication method is required: "
                "ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN"
            )


def configure_logging(level: str) -> None:
    """Configure application-wide logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_settings() -> Settings:
    """Load settings from environment, validate, and configure logging."""
    settings = Settings()  # type: ignore[call-arg]  # Values come from the environment.
    settings.validate_auth()
    configure_logging(settings.log_level)
    return settings
