"""AI-owned local resource budgets; shared wire limits are not configurable."""

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretBytes, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class V2Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_V2_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    provider: Literal["disabled", "stub", "openai"] = "disabled"
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key")
    )
    model: str = Field(default="gpt-6-luna", pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    service_tier: Literal["default"] = "default"
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    receipt_db_path: Path | None = None
    hmac_keys: dict[str, SecretBytes] = Field(default_factory=dict)
    active_hmac_key_version: str | None = None
    provider_timeout_seconds: float = Field(default=20, gt=0, le=25, allow_inf_nan=False)
    total_timeout_seconds: float = Field(default=26, gt=0, le=26, allow_inf_nan=False)
    body_timeout_seconds: float = Field(default=2, gt=0, le=2, allow_inf_nan=False)
    worker_timeout_seconds: float = Field(default=1, gt=0, le=1, allow_inf_nan=False)
    store_lock_timeout_seconds: float = Field(default=0.25, ge=0, le=0.5, allow_inf_nan=False)

    @model_validator(mode="after")
    def enabled_configuration(self) -> Self:
        if self.provider != "disabled":
            if (
                self.receipt_db_path is None
                or not self.receipt_db_path.is_absolute()
                or self.active_hmac_key_version not in self.hmac_keys
                or any(not k or len(v.get_secret_value()) < 32 for k, v in self.hmac_keys.items())
            ):
                raise ValueError(
                    "v2 requires an absolute receipt path and dedicated versioned HMAC keys"
                )
            if self.store_lock_timeout_seconds >= self.worker_timeout_seconds:
                raise ValueError("SQLite lock wait must be below the worker deadline")
        if self.provider == "openai":
            key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
            if not key or not key.isascii() or any(c.isspace() for c in key):
                raise ValueError("OpenAI requires a separate nonblank API key")
            if any(key.encode() == secret.get_secret_value() for secret in self.hmac_keys.values()):
                raise ValueError("OpenAI and HMAC keys must be separate")
        return self
