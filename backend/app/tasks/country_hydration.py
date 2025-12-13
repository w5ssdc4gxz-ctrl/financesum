"""Background task for retrying failed country hydrations."""
from app.tasks.celery_app import celery_app
from app.models.database import get_supabase_client
from app.services.eodhd_client import hydrate_country_with_eodhd
from app.services.country_hydration_queue import (
    get_pending_hydrations,
    mark_hydrated,
    mark_attempt_failed,
    remove_expired,
)
from app.services.local_cache import fallback_companies, save_fallback_companies
from app.api.companies import _supabase_configured
from app.config import get_settings


@celery_app.task(bind=True)
def process_pending_country_hydrations(self, batch_size: int = 5):
    """
    Process pending country hydrations in background.

    This task should be scheduled to run periodically (e.g., every 5 minutes)
    or called manually when needed.

    Args:
        batch_size: Number of pending hydrations to process in one run

    Returns:
        Dict with processing statistics
    """
    settings = get_settings()

    # Clean up expired entries first (max 5 retries)
    expired = remove_expired(max_retries=5)
    if expired:
        print(f"Removed {len(expired)} companies that exceeded max hydration retries")

    # Get pending items
    pending = get_pending_hydrations(limit=batch_size)
    if not pending:
        return {"processed": 0, "message": "No pending hydrations"}

    processed = 0
    succeeded = 0

    for item in pending:
        try:
            country = hydrate_country_with_eodhd(item.ticker, item.exchange)

            if country:
                # Persist to database
                if _supabase_configured(settings):
                    try:
                        supabase = get_supabase_client()
                        supabase.table("companies").update({"country": country}).eq("id", item.company_id).execute()
                    except Exception as db_exc:
                        print(f"Failed to persist country for {item.ticker}: {db_exc}")
                else:
                    # Update fallback cache
                    if item.company_id in fallback_companies:
                        fallback_companies[item.company_id]["country"] = country
                        save_fallback_companies()

                mark_hydrated(item.company_id)
                succeeded += 1
                print(f"Background hydration succeeded for {item.ticker}: {country}")
            else:
                mark_attempt_failed(item.company_id)
                print(f"Background hydration failed for {item.ticker} (attempt {item.retry_count + 1})")

            processed += 1

        except Exception as exc:
            mark_attempt_failed(item.company_id)
            print(f"Background hydration error for {item.ticker}: {exc}")
            processed += 1

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": processed - succeeded
    }
