"""Database connection and client."""
from functools import lru_cache
import inspect
import os

from supabase import create_client, Client
from supabase.lib import client_options as supabase_client_options

from app.config import get_settings


def _timeout_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _build_supabase_options():
    # Keep dashboard/overview responsive when Supabase is slow/unreachable.
    postgrest_timeout = _timeout_from_env("SUPABASE_POSTGREST_TIMEOUT_SECONDS", 15.0)
    storage_timeout = _timeout_from_env("SUPABASE_STORAGE_TIMEOUT_SECONDS", 60.0)
    function_timeout = _timeout_from_env("SUPABASE_FUNCTION_TIMEOUT_SECONDS", 15.0)

    options_cls = getattr(supabase_client_options, "SyncClientOptions", None) or getattr(
        supabase_client_options, "ClientOptions"
    )
    kwargs = {
        "postgrest_client_timeout": postgrest_timeout,
        "storage_client_timeout": storage_timeout,
    }

    sig = inspect.signature(options_cls)
    if "function_client_timeout" in sig.parameters:
        kwargs["function_client_timeout"] = function_timeout

    return options_cls(**kwargs)


@lru_cache()
def get_supabase_client() -> Client:
    """Get Supabase client instance."""
    settings = get_settings()
    return create_client(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_service_role_key,
        options=_build_supabase_options(),
    )


def get_supabase_anon_client() -> Client:
    """Get Supabase client with anon key (for public operations)."""
    settings = get_settings()
    return create_client(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_anon_key,
        options=_build_supabase_options(),
    )















