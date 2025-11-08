"""Configuration settings for the application."""
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent


class Settings(BaseSettings):
    """Application settings."""
    
    # Supabase configuration
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    
    # Gemini AI configuration
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    
    # Redis configuration (defaults to localhost)
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # CORS configuration
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    )
    cors_origin_regex: str | None = os.getenv("CORS_ORIGIN_REGEX")
    
    # EODHD API configuration (Required for financial data)
    eodhd_api_key: str = os.getenv("EODHD_API_KEY", "demo")
    edgar_user_agent: str = os.getenv(
        "EDGAR_USER_AGENT",
        "FinancesumApp/1.0 (financesum@example.com)",
    )
    
    # API configuration
    api_version: str = "v1"
    debug: bool = os.getenv("DEBUG", "False").lower() == "true"
    
    # File storage
    data_dir: str = os.getenv("DATA_DIR", "./data")
    temp_dir: str = os.getenv("TEMP_DIR", "./temp")
    
    class Config:
        env_file = str((ROOT_DIR / ".env").resolve())
        case_sensitive = False


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

    if not settings.gemini_api_key:
        secret = _fetch_secret_from_supabase(settings, "GEMINI_API_KEY")
        if secret:
            settings.gemini_api_key = secret

    return settings

