"""Configuration settings for the application."""
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent.parent
REPO_ROOT_DIR = BACKEND_DIR.parent

ENV_FILES = [
    REPO_ROOT_DIR / ".env",
    BACKEND_DIR / ".env",
]


def ensure_env_loaded() -> None:
    """Best-effort dotenv loader for dev ergonomics.

    Pydantic settings reads env files at process start. During local development,
    users often edit `.env` without restarting the backend; this helper lets
    endpoints re-load missing variables safely without overriding existing ones.
    """
    try:
        from dotenv import dotenv_values
    except Exception:  # noqa: BLE001
        return

    for path in ENV_FILES:
        try:
            if not path.exists():
                continue

            values = dotenv_values(path)
            for key, value in values.items():
                if value is None:
                    continue
                # Only write when missing/blank so real env vars can still override `.env`.
                if os.environ.get(key, ""):
                    continue
                os.environ[key] = value
        except Exception:  # noqa: BLE001
            continue

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "https://financesums.com",
    "https://www.financesums.com",
    "https://financesums-frontend-1093972319438.europe-west1.run.app",
]
DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https://("
    r"(?:[a-z0-9-]+\.)*financesums\.com"
    r"|(?:[a-z0-9-]+---)?financesums-frontend-[a-z0-9-]+\.a\.run\.app"
    r"|financesums-frontend-\d+\.[a-z0-9-]+\.run\.app"
    r")$"
)


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=[str(path.resolve()) for path in ENV_FILES],
        case_sensitive=False,
        extra="ignore",
    )
    
    # Supabase configuration
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    
    # OpenAI GPT-5.2 configuration (primary AI provider)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # OpenAI retry configuration
    openai_max_retries: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum retry attempts for rate-limited OpenAI requests"
    )
    openai_initial_wait: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Initial wait time in seconds before first retry"
    )
    openai_max_wait: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Maximum wait time in seconds between retries"
    )

    # Deprecated Gemini config placeholders retained only for non-summary legacy paths.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_max_retries: int = Field(default=2, ge=1, le=10)
    gemini_initial_wait: int = Field(default=1, ge=1, le=10)
    gemini_max_wait: int = Field(default=60, ge=10, le=300)

    # Redis configuration (defaults to localhost)
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Stripe billing configuration
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_publishable_key: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_lookup_key: str = os.getenv("STRIPE_PRICE_LOOKUP_KEY", "")
    stripe_price_id: str = os.getenv("STRIPE_PRICE_ID", "")
    site_url: str = os.getenv("SITE_URL", "")

    # CORS configuration
    cors_origins: List[str] = Field(default_factory=lambda: DEFAULT_CORS_ORIGINS.copy())
    cors_origin_regex: str | None = Field(default=DEFAULT_CORS_ORIGIN_REGEX)
    cors_allow_all: bool = Field(default=False)
    
    # EODHD API configuration (Required for financial data)
    eodhd_api_key: str = os.getenv("EODHD_API_KEY", "demo")
    edgar_user_agent: str = os.getenv(
        "EDGAR_USER_AGENT",
        "FinancesumApp/1.0 (financesum@example.com)",
    )
    
    # API configuration
    api_version: str = "v1"
    debug: bool = os.getenv("DEBUG", "False").lower() == "true"
    enable_growth_assessment: bool = os.getenv("ENABLE_GROWTH_ASSESSMENT", "False").lower() == "true"
    
    # File storage
    data_dir: str = os.getenv("DATA_DIR", "./data")
    temp_dir: str = os.getenv("TEMP_DIR", "./temp")


def _fetch_secret_from_supabase(settings: Settings, secret_key: str) -> Optional[str]:
    """Load a secret from Supabase config table using the service role key."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None

    try:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        response = (
            client.table("app_config")
            .select("value")
            .eq("key", secret_key)
            .single()
            .execute()
        )
        if response.data:
            return response.data.get("value")
    except Exception as exc:
        print(f"Unable to fetch {secret_key} from Supabase: {exc}")
    return None


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()

    # OpenAI API key resolution (highest to lowest priority):
    # 1) OPENAI_API_KEY env var (already loaded by Settings())
    # 2) OPENAI-API-KEY env alias (legacy env naming convention)
    #
    # Intentionally DO NOT load OPENAI keys from Supabase app_config.
    # OpenAI credentials must be provided via environment variables.
    if not settings.openai_api_key:
        legacy_env = (os.getenv("OPENAI-API-KEY") or "").strip()
        if legacy_env:
            settings.openai_api_key = legacy_env

    # Intentionally do not auto-load GEMINI_API_KEY from Supabase.
    # Production summary pipeline is OpenAI-only.

    if not settings.stripe_secret_key:
        secret = _fetch_secret_from_supabase(settings, "STRIPE_SECRET_KEY")
        if secret:
            settings.stripe_secret_key = secret

    if not settings.stripe_webhook_secret:
        secret = _fetch_secret_from_supabase(settings, "STRIPE_WEBHOOK_SECRET")
        if secret:
            settings.stripe_webhook_secret = secret

    return settings
