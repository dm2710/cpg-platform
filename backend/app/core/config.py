import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file():
    """
    Skip .env entirely if DATABASE_URL is already set in the environment
    (i.e. we are running inside Docker where compose injects it directly).
    This prevents a stale .env on disk from overriding Docker env vars.
    """
    if os.environ.get("DATABASE_URL"):
        return None          # no .env file — use env vars only
    return ".env"            # local dev without Docker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ─────────────────────────────────────────
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    secret_key: str = "dev-secret-change-in-production"

    # ── Database ─────────────────────────────────────────────
    # Default = Docker service name + internal port.
    # Override DATABASE_URL for local dev outside Docker.
    database_url: str = Field(
        default="postgresql+psycopg2://cpg:cpg_secret@postgres:5432/cpg_platform"
    )
    async_database_url: str = Field(
        default="postgresql+asyncpg://cpg:cpg_secret@postgres:5432/cpg_platform"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_echo: bool = False

    # ── External APIs ─────────────────────────────────────────
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # ── Ingestion ─────────────────────────────────────────────
    ingestion_batch_size: int = 500
    late_arrival_soft_days: int = 3
    late_arrival_hard_days: int = 7
    late_arrival_review_days: int = 30

    # ── API ───────────────────────────────────────────────────
    api_v1_prefix: str = "/api/v1"
    project_name: str = "CPG Predictive Intelligence Platform"
    version: str = "2.0.0"

    # ── Security ─────────────────────────────────────────────
    # secret_key above is reused as the JWT signing key. In production
    # this MUST be overridden via the SECRET_KEY env var (a long random
    # value), never left at the dev default -- see docker-compose.yml
    # and .env.example for the secrets-management pattern.
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60
    jwt_refresh_token_days: int = 14
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ── Automated retraining ───────────────────────────────────
    retraining_enabled: bool = True
    retraining_cron: str = "0 3 * * *"       # daily at 03:00
    retraining_drift_mape_threshold: float = 20.0   # trigger retrain if MAPE exceeds this
    retraining_min_new_rows: int = 50        # skip retrain if fewer than this many new rows since last run

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field
    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
