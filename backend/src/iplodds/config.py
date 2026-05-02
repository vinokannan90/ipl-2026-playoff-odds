"""Application configuration via environment + Key Vault.

Secrets are NEVER read from environment in production — Container Apps
injects them as Key Vault references, which appear as env vars at runtime
but are sourced from Key Vault with managed-identity access.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IPLODDS_", env_file=".env", extra="ignore")

    # --- Runtime ---
    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    # --- CORS allowlist (comma-separated origins) ---
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    # --- Upstream data feed ---
    iplt20_competition_id: int = 284
    iplt20_base: str = "https://scores.iplt20.com/ipl/feeds"
    upstream_timeout_s: float = 10.0

    # --- Cache (Azure Blob; falls back to in-memory in dev) ---
    blob_account_url: str = ""  # https://<acct>.blob.core.windows.net
    blob_container: str = "cache"
    cache_ttl_standings_s: int = 300
    cache_ttl_schedule_s: int = 300
    cache_ttl_live_s: int = 30        # short TTL for live-match scorecard fetches
    cache_ttl_priors_s: int = 86400  # 1 day

    # --- LLM (GitHub Models, OpenAI-compatible) ---
    llm_provider: Literal["github", "azure_openai", "none"] = "github"
    github_models_endpoint: str = "https://models.inference.ai.azure.com"
    github_models_token: str = ""  # Key Vault ref in prod
    github_models_model: str = "gpt-4o-mini"

    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str = ""

    # --- Feature flags ---
    feature_priors: bool = True
    feature_agent: bool = True
    feature_leverage: bool = True
    feature_scout: bool = False  # OFF by default; needs vetted news source
    feature_daily_job: bool = True

    # --- Rate limiting ---
    rate_limit_default: str = "30/minute"
    rate_limit_agent: str = "10/minute"

    # --- Misc ---
    request_max_body_bytes: int = 16 * 1024  # 16 KB

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
