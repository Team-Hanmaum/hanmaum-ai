from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_API_KEY = "local-development-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "prod"] = "local"
    ai_provider: Literal["disabled", "stub"] = "disabled"
    ai_internal_api_key: SecretStr = SecretStr(LOCAL_API_KEY)
    analysis_timeout_seconds: float = Field(default=20, gt=0, le=25)

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        key = self.ai_internal_api_key.get_secret_value()
        if not key.strip():
            raise ValueError("AI_INTERNAL_API_KEY must not be empty")
        if self.app_env == "prod":
            if key == LOCAL_API_KEY or len(key) < 32:
                raise ValueError("Production requires a separate API key of at least 32 characters")
            if self.ai_provider == "stub":
                raise ValueError("The stub provider is restricted to local/test environments")
        return self
