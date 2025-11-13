"""Helpers for handling Supabase errors gracefully."""


def is_supabase_table_missing_error(error: Exception) -> bool:
    """
    Return True when Supabase reports that a referenced table is missing.

    This typically surfaces as PostgREST error code PGRST205 with a message like
    "Could not find the table 'public.xyz' in the schema cache".
    We also guard against common phrasing like "relation ... does not exist".
    """
    try:
        message = str(error)
    except Exception:  # pragma: no cover - extremely defensive
        return False

    if not message:
        return False

    lowered = message.lower()
    return (
        "could not find the table" in lowered
        or "pgrst205" in lowered
        or "does not exist" in lowered
    )
