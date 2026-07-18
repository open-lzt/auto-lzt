"""Bot settings — its own env surface, separate from the API's.

``enabled`` defaults False and ``admin_ids`` defaults empty: an operator who sets a token but
forgets the admin list gets a bot that answers nobody, not a bot that answers everybody. That is
the only sensible direction for a control surface that can spend money.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LZT_FLOW_BOT_", env_file=".env", extra="ignore")

    token: SecretStr = Field(default=SecretStr(""))
    admin_ids: frozenset[int] = Field(default=frozenset())
    enabled: bool = Field(default=False)

    # The bot talks to the API over loopback rather than importing the services directly: it is a
    # client of the same surface the web UI uses, so a capability the bot has is one the API
    # already exposes and audits.
    api_base_url: str = Field(default="http://127.0.0.1:8000")
    api_key: str = Field(default="")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        """Accept "1,2,3" from .env — pydantic would otherwise demand a JSON array."""
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value

    def is_configured(self) -> bool:
        """Whether starting the bot could do anything useful. A bot with a token and no admins is
        not configured; it is a bot nobody can talk to."""
        return self.enabled and bool(self.token.get_secret_value()) and bool(self.admin_ids)
