from typing import Literal
from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central typed settings, read from the environment, `.env` or `.secrets`.

    Every field is optional at load time so that commands which do not talk to
    GitHub (Silver/Gold processing, AI analysis) can still load settings.
    Commands that need a value validate it themselves (see
    `coops.etl.bronze_extract.main`).

    Env vars: GITHUB_TOKEN (or COOPS_GITHUB_TOKEN), GITHUB_ORG (or COOPS_ORG),
    GEMINI_API_KEY (or GOOGLE_API_KEY), GEMINI_MODEL, GITHUB_API_URL,
    COOPS_STORAGE, MONGO_URI, RAW_MAX_AGE_SECONDS, TENANT_MODE.

    GitHub rejects Actions secrets and variables whose names start with
    GITHUB_, so the COOPS_ names can be mapped 1:1 from `secrets`/`vars`.
    Precedence: the environment beats `.env`/`.secrets`, whichever name it
    uses (GITHUB_ORG in the environment wins over COOPS_ORG in `.secrets`).
    Within the same source, the COOPS_ name wins over the GITHUB_ name; the
    two files are read as a single source, `.secrets` overriding `.env`.

    Empty values count as unset, because an undefined Actions secret or
    variable expands to an empty string.
    """

    github_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COOPS_GITHUB_TOKEN", "GITHUB_TOKEN"),
    )
    github_org: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COOPS_ORG", "GITHUB_ORG"),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    # gemini-2.5-flash-lite is no longer available to new API keys.
    gemini_model: str = "gemini-3.5-flash-lite"
    # Placeholders for later phases; not read by the pipeline yet.
    github_api_url: str = "https://api.github.com"
    coops_storage: str = "data"
    mongo_uri: str | None = None
    # How long a raw-layer document stays "fresh" before Bronze re-fetches the
    # API instead of reading it. None/0 would disable the raw read short-circuit
    # entirely; a positive value is what makes re-processing free (#113).
    raw_max_age_seconds: int = 3600
    tenant_mode: Literal["single", "multi"] = "single"

    model_config = SettingsConfigDict(
        env_file=(".env", ".secrets"),
        env_ignore_empty=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
