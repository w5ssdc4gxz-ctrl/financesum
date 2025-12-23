"""Stripe Billing + Checkout integration endpoints."""

from __future__ import annotations

import asyncio
import calendar
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import CurrentUser, get_current_user
from app.config import DEFAULT_CORS_ORIGINS, ensure_env_loaded, get_settings
from app.models.database import get_supabase_client
from app.services.billing_usage import get_summary_usage_status

router = APIRouter()


def _require_stripe_settings() -> None:
    ensure_env_loaded()
    settings = get_settings()
    secret_key = (os.getenv("STRIPE_SECRET_KEY") or settings.stripe_secret_key or "").strip()
    if not secret_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured (missing STRIPE_SECRET_KEY).")

    stripe.api_key = secret_key
    # Keep retries small to avoid duplicate webhooks / sessions on flaky networks.
    stripe.max_network_retries = 2


def _resolve_origin_site_url(request: Request) -> str:
    """Resolve a safe site URL used for Stripe redirects."""
    ensure_env_loaded()
    settings = get_settings()
    site_url = (os.getenv("SITE_URL") or settings.site_url or "").strip()
    if site_url:
        return site_url.rstrip("/")

    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin:
        # Only trust allowed CORS origins (prevents attackers setting an arbitrary origin).
        allowed_origins = settings.cors_origins or DEFAULT_CORS_ORIGINS.copy()
        if settings.cors_allow_all or "*" in allowed_origins:
            return origin
        if origin in allowed_origins:
            return origin

    # Fallback for local/dev or direct calls.
    return "http://localhost:3000"


def _coerce_relative_path(value: str | None, *, default: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return default
    if not candidate.startswith("/"):
        raise HTTPException(status_code=400, detail="Paths must start with '/'.")
    if "://" in candidate:
        raise HTTPException(status_code=400, detail="Absolute URLs are not allowed.")
    return candidate


def _ts_to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        return _parse_iso_datetime(value)
    return None


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _add_interval(value: datetime, *, interval: str, count: int) -> datetime:
    if interval == "day":
        return value + timedelta(days=count)
    if interval == "week":
        return value + timedelta(weeks=count)
    if interval == "month":
        return _add_months(value, count)
    if interval == "year":
        return _add_months(value, count * 12)
    return value


def _extract_recurring_interval(subscription: Any) -> tuple[Optional[str], int]:
    items = getattr(subscription, "items", None)
    data = getattr(items, "data", None) if items is not None else None
    first = data[0] if data else None
    price = getattr(first, "price", None) if first is not None else None
    recurring = getattr(price, "recurring", None) if price is not None else None
    interval = getattr(recurring, "interval", None) if recurring is not None else None
    interval_count = getattr(recurring, "interval_count", None) if recurring is not None else None
    if not interval:
        return None, 1
    try:
        count = int(interval_count or 1)
    except (TypeError, ValueError):
        count = 1
    return str(interval), max(count, 1)


def _resolve_period_bounds(subscription: Any) -> tuple[Optional[datetime], Optional[datetime]]:
    start = _parse_timestamp(getattr(subscription, "current_period_start", None))
    end = _parse_timestamp(getattr(subscription, "current_period_end", None))

    if start is None:
        start = _parse_timestamp(getattr(subscription, "billing_cycle_anchor", None))
    if start is None:
        start = _parse_timestamp(getattr(subscription, "start_date", None))
    if start is None:
        start = _parse_timestamp(getattr(subscription, "created", None))

    if end is None and start is not None:
        interval, count = _extract_recurring_interval(subscription)
        if interval:
            end = _add_interval(start, interval=interval, count=count)

    return start, end


async def _resolve_price_id() -> str:
    ensure_env_loaded()
    settings = get_settings()
    price_id = (os.getenv("STRIPE_PRICE_ID") or settings.stripe_price_id or "").strip()
    if price_id:
        return price_id

    lookup_key = (os.getenv("STRIPE_PRICE_LOOKUP_KEY") or settings.stripe_price_lookup_key or "").strip()
    if not lookup_key:
        secret_key = (os.getenv("STRIPE_SECRET_KEY") or settings.stripe_secret_key or "").strip()
        if secret_key.startswith("sk_test_"):
            lookup_key = "financesum_pro_monthly"
        else:
            raise HTTPException(
                status_code=503,
                detail="Stripe price is not configured (set STRIPE_PRICE_ID or STRIPE_PRICE_LOOKUP_KEY).",
            )

    _require_stripe_settings()
    try:
        prices = await asyncio.to_thread(
            stripe.Price.list,
            lookup_keys=[lookup_key],
            limit=1,
            expand=["data.product"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to fetch Stripe price: {exc}") from exc

    data = getattr(prices, "data", None) or []
    if not data:
        if secret_key.startswith("sk_test_") and lookup_key == "financesum_pro_monthly":
            try:
                created = await asyncio.to_thread(
                    stripe.Price.create,
                    currency="usd",
                    unit_amount=2000,
                    recurring={"interval": "month"},
                    product_data={"name": "FinanceSum Pro"},
                    lookup_key=lookup_key,
                    metadata={"plan": "pro"},
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Unable to create Stripe test price: {exc}") from exc
            created_id = getattr(created, "id", None)
            if not created_id:
                raise HTTPException(status_code=502, detail="Stripe did not return a price ID when creating test price.")
            return str(created_id)

        raise HTTPException(status_code=503, detail=f"No Stripe price found for lookup key '{lookup_key}'.")
    resolved = getattr(data[0], "id", None)
    if not resolved:
        raise HTTPException(status_code=503, detail=f"Stripe returned an invalid price for lookup key '{lookup_key}'.")
    return str(resolved)


def _looks_like_supabase_table_missing_error(error: Exception) -> bool:
    """Return True when PostgREST indicates the billing tables don't exist."""
    try:
        message = str(error)
    except Exception:  # noqa: BLE001
        return False

    lowered = message.lower()
    return (
        "could not find the table" in lowered
        or "pgrst205" in lowered
        or ("relation" in lowered and "does not exist" in lowered)
    )


def _looks_like_invalid_supabase_apikey(error: Exception) -> bool:
    try:
        message = str(error)
    except Exception:  # noqa: BLE001
        return False
    lowered = message.lower()
    return "invalid api key" in lowered or ("api key" in lowered and "invalid" in lowered)


def _try_get_supabase() -> Any | None:
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_role_key):
        return None
    supabase = get_supabase_client()

    # Fail fast with a clear error when billing tables haven't been deployed yet.
    try:
        supabase.table("billing_customers").select("user_id").limit(1).execute()
        supabase.table("billing_subscriptions").select("stripe_subscription_id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        # Billing can still run without Supabase tables by using Stripe as source of truth.
        if _looks_like_invalid_supabase_apikey(exc) or _looks_like_supabase_table_missing_error(exc):
            return None
        print(f"Billing: unable to query Supabase billing tables: {exc}")
        return None

    return supabase


def _get_stripe_customer_id(supabase: Any | None, *, user_id: str) -> Optional[str]:
    if supabase is None:
        return None
    try:
        response = (
            supabase.table("billing_customers")
            .select("stripe_customer_id")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        data = response.data
        if isinstance(data, dict):
            value = data.get("stripe_customer_id")
            return str(value) if value else None
    except Exception as exc:  # noqa: BLE001
        print(f"Billing: unable to load billing customer for user {user_id}: {exc}")
    return None


def _find_user_id_by_customer_id(supabase: Any | None, *, stripe_customer_id: str) -> Optional[str]:
    if supabase is None:
        return None
    try:
        response = (
            supabase.table("billing_customers")
            .select("user_id")
            .eq("stripe_customer_id", stripe_customer_id)
            .maybe_single()
            .execute()
        )
        data = response.data
        if isinstance(data, dict):
            value = data.get("user_id")
            return str(value) if value else None
    except Exception as exc:  # noqa: BLE001
        print(f"Billing: unable to map Stripe customer {stripe_customer_id} to user: {exc}")
    return None


def _upsert_billing_customer(
    supabase: Any | None,
    *,
    user_id: str,
    stripe_customer_id: str,
    email: Optional[str],
) -> None:
    if supabase is None:
        return
    try:
        supabase.table("billing_customers").upsert(
            {
                "user_id": user_id,
                "stripe_customer_id": stripe_customer_id,
                "email": email,
            },
            on_conflict="user_id",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"Billing: failed to upsert billing customer for user {user_id}: {exc}")


def _upsert_subscription_from_stripe(
    supabase: Any | None,
    *,
    user_id: str,
    subscription: Any,
) -> None:
    if supabase is None:
        return
    stripe_subscription_id = str(getattr(subscription, "id", "") or "")
    if not stripe_subscription_id:
        return

    customer = getattr(subscription, "customer", None)
    stripe_customer_id = str(getattr(customer, "id", customer) or "")

    price_id: Optional[str] = None
    product_id: Optional[str] = None
    try:
        items = getattr(subscription, "items", None)
        data = getattr(items, "data", None) if items is not None else None
        first = data[0] if data else None
        price = getattr(first, "price", None) if first is not None else None
        price_id = str(getattr(price, "id", "") or "") or None
        product_id = str(getattr(price, "product", "") or "") or None
    except Exception:  # noqa: BLE001
        price_id = None
        product_id = None

    cancel_at_period_end = bool(getattr(subscription, "cancel_at_period_end", False))
    cancel_at = getattr(subscription, "cancel_at", None)
    if not cancel_at_period_end and cancel_at:
        cancel_at_period_end = True

    period_start, period_end = _resolve_period_bounds(subscription)

    record: Dict[str, Any] = {
        "stripe_subscription_id": stripe_subscription_id,
        "user_id": user_id,
        "stripe_customer_id": stripe_customer_id or None,
        "status": str(getattr(subscription, "status", "") or ""),
        "price_id": price_id,
        "product_id": product_id,
        "current_period_start": period_start.isoformat() if period_start else None,
        "current_period_end": period_end.isoformat() if period_end else None,
        "cancel_at_period_end": cancel_at_period_end,
        "canceled_at": _ts_to_iso(getattr(subscription, "canceled_at", None)),
        "ended_at": _ts_to_iso(getattr(subscription, "ended_at", None)),
        "trial_start": _ts_to_iso(getattr(subscription, "trial_start", None)),
        "trial_end": _ts_to_iso(getattr(subscription, "trial_end", None)),
        "livemode": bool(getattr(subscription, "livemode", False)),
        "metadata": dict(getattr(subscription, "metadata", {}) or {}),
    }

    try:
        supabase.table("billing_subscriptions").upsert(
            record,
            on_conflict="stripe_subscription_id",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"Billing: failed to upsert subscription {stripe_subscription_id}: {exc}")


def _stripe_subscription_to_record(subscription: Any, *, user_id: str) -> Dict[str, Any]:
    customer = getattr(subscription, "customer", None)
    stripe_customer_id = str(getattr(customer, "id", customer) or "")

    price_id: Optional[str] = None
    product_id: Optional[str] = None
    try:
        items = getattr(subscription, "items", None)
        data = getattr(items, "data", None) if items is not None else None
        first = data[0] if data else None
        price = getattr(first, "price", None) if first is not None else None
        price_id = str(getattr(price, "id", "") or "") or None
        product_id = str(getattr(price, "product", "") or "") or None
    except Exception:  # noqa: BLE001
        price_id = None
        product_id = None

    cancel_at_period_end = bool(getattr(subscription, "cancel_at_period_end", False))
    cancel_at = getattr(subscription, "cancel_at", None)
    if not cancel_at_period_end and cancel_at:
        cancel_at_period_end = True

    period_start, period_end = _resolve_period_bounds(subscription)

    return {
        "stripe_subscription_id": str(getattr(subscription, "id", "") or ""),
        "user_id": user_id,
        "stripe_customer_id": stripe_customer_id or None,
        "status": str(getattr(subscription, "status", "") or ""),
        "price_id": price_id,
        "product_id": product_id,
        "current_period_start": period_start.isoformat() if period_start else None,
        "current_period_end": period_end.isoformat() if period_end else None,
        "cancel_at_period_end": cancel_at_period_end,
        "canceled_at": _ts_to_iso(getattr(subscription, "canceled_at", None)),
        "ended_at": _ts_to_iso(getattr(subscription, "ended_at", None)),
        "trial_start": _ts_to_iso(getattr(subscription, "trial_start", None)),
        "trial_end": _ts_to_iso(getattr(subscription, "trial_end", None)),
        "livemode": bool(getattr(subscription, "livemode", False)),
        "metadata": dict(getattr(subscription, "metadata", {}) or {}),
    }


async def _search_subscriptions_by_user_id(user_id: str) -> list[Any]:
    query = f"metadata['user_id']:'{user_id}'"
    try:
        results = await asyncio.to_thread(
            stripe.Subscription.search,
            query=query,
            limit=20,
            expand=["data.items.data.price"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to query Stripe subscriptions: {exc}") from exc

    data = getattr(results, "data", None) or []
    return list(data)


async def _search_subscriptions_by_customer_id(customer_id: str) -> list[Any]:
    try:
        results = await asyncio.to_thread(
            stripe.Subscription.list,
            customer=customer_id,
            status="all",
            limit=20,
            expand=["data.items.data.price"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to query Stripe subscriptions: {exc}") from exc

    data = getattr(results, "data", None) or []
    return list(data)


def _pick_best_subscription(subscriptions: list[Any]) -> Any | None:
    if not subscriptions:
        return None
    active = [sub for sub in subscriptions if str(getattr(sub, "status", "") or "") in {"active", "trialing"}]
    candidates = active or subscriptions
    return max(candidates, key=lambda sub: int(getattr(sub, "created", 0) or 0))


async def _resolve_stripe_customer_id_for_user(user: CurrentUser, *, supabase: Any | None) -> str | None:
    stripe_customer_id = _get_stripe_customer_id(supabase, user_id=user.id)
    if stripe_customer_id:
        return stripe_customer_id

    subscriptions = await _search_subscriptions_by_user_id(user.id)
    chosen = _pick_best_subscription(subscriptions)
    if chosen is None:
        if user.email:
            try:
                customers = await asyncio.to_thread(
                    stripe.Customer.list,
                    email=user.email,
                    limit=10,
                )
            except Exception:  # noqa: BLE001
                customers = None

            customer_data = getattr(customers, "data", None) or []
            for customer in customer_data:
                customer_id = str(getattr(customer, "id", "") or "")
                if not customer_id:
                    continue
                metadata = getattr(customer, "metadata", None) or {}
                meta_user_id = str(getattr(metadata, "get", lambda *_: None)("user_id") or "")
                if meta_user_id and meta_user_id != user.id:
                    continue
                return customer_id

        return None
    customer = getattr(chosen, "customer", None)
    customer_id = str(getattr(customer, "id", customer) or "")
    return customer_id or None


async def _ensure_customer_metadata(*, customer_id: str, user_id: str) -> None:
    try:
        await asyncio.to_thread(
            stripe.Customer.modify,
            customer_id,
            metadata={"user_id": user_id},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Billing: unable to update Stripe customer metadata for {customer_id}: {exc}")


class BillingConfigResponse(BaseModel):
    publishable_key: str = Field(default="")
    price_lookup_key: str = Field(default="")
    price_id: str = Field(default="")
    secret_key_configured: bool = Field(default=False)
    webhook_configured: bool = Field(default=False)
    mode: str = Field(default="")


class UsageSummaryResponse(BaseModel):
    plan: Literal["free", "pro"] = "free"
    limit: int = 0
    used: int = 0
    remaining: int = 0
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    subscription_status: Optional[str] = None
    cancel_at_period_end: Optional[bool] = None
    is_pro: bool = False
    billing_unavailable: bool = False


@router.get("/config", response_model=BillingConfigResponse)
async def get_billing_config() -> BillingConfigResponse:
    ensure_env_loaded()
    settings = get_settings()
    secret_key = (os.getenv("STRIPE_SECRET_KEY") or settings.stripe_secret_key or "").strip()
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or settings.stripe_webhook_secret or "").strip()
    mode = ""
    if secret_key.startswith("sk_test_"):
        mode = "test"
    elif secret_key.startswith("sk_live_"):
        mode = "live"
    return BillingConfigResponse(
        publishable_key=(os.getenv("STRIPE_PUBLISHABLE_KEY") or settings.stripe_publishable_key or "").strip(),
        price_lookup_key=(os.getenv("STRIPE_PRICE_LOOKUP_KEY") or settings.stripe_price_lookup_key or "").strip(),
        price_id=(os.getenv("STRIPE_PRICE_ID") or settings.stripe_price_id or "").strip(),
        secret_key_configured=bool(secret_key),
        webhook_configured=bool(webhook_secret),
        mode=mode,
    )


@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    user: CurrentUser = Depends(get_current_user),
) -> UsageSummaryResponse:
    usage = get_summary_usage_status(user.id)
    return UsageSummaryResponse(
        plan=usage.plan,
        limit=usage.limit,
        used=usage.used,
        remaining=usage.remaining,
        period_start=usage.period_start.isoformat() if usage.period_start else None,
        period_end=usage.period_end.isoformat() if usage.period_end else None,
        subscription_status=usage.subscription_status,
        cancel_at_period_end=usage.cancel_at_period_end,
        is_pro=usage.is_pro,
        billing_unavailable=usage.billing_unavailable,
    )


class CreateCheckoutSessionRequest(BaseModel):
    plan: Literal["pro"] = "pro"
    success_path: Optional[str] = "/billing/success"
    cancel_path: Optional[str] = "/billing/cancel"


class CreateCheckoutSessionResponse(BaseModel):
    id: str
    url: str


@router.post("/create-checkout-session", response_model=CreateCheckoutSessionResponse)
async def create_checkout_session(
    request: Request,
    payload: CreateCheckoutSessionRequest,
    user: CurrentUser = Depends(get_current_user),
) -> CreateCheckoutSessionResponse:
    if payload.plan != "pro":
        raise HTTPException(status_code=400, detail="Unsupported plan.")

    _require_stripe_settings()
    supabase = _try_get_supabase()

    site_url = _resolve_origin_site_url(request)
    success_path = _coerce_relative_path(payload.success_path, default="/billing/success")
    cancel_path = _coerce_relative_path(payload.cancel_path, default="/billing/cancel")

    success_url = f"{site_url}{success_path}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{site_url}{cancel_path}"

    price_id = await _resolve_price_id()

    stripe_customer_id = await _resolve_stripe_customer_id_for_user(user, supabase=supabase)
    session_params: Dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": user.id,
        "metadata": {"user_id": user.id, "plan": payload.plan},
        "subscription_data": {"metadata": {"user_id": user.id, "plan": payload.plan}},
        "line_items": [{"quantity": 1, "price": price_id}],
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if stripe_customer_id:
        session_params["customer"] = stripe_customer_id
    elif user.email:
        session_params["customer_email"] = user.email

    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            **session_params,
            idempotency_key=str(uuid4()),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to create Stripe Checkout session: {exc}") from exc

    url = getattr(session, "url", None)
    session_id = getattr(session, "id", None)
    if not url or not session_id:
        raise HTTPException(status_code=502, detail="Stripe did not return a checkout session URL.")

    return CreateCheckoutSessionResponse(id=str(session_id), url=str(url))


class CreatePortalSessionResponse(BaseModel):
    url: str


@router.post("/create-portal-session", response_model=CreatePortalSessionResponse)
async def create_portal_session(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> CreatePortalSessionResponse:
    _require_stripe_settings()
    supabase = _try_get_supabase()

    stripe_customer_id = await _resolve_stripe_customer_id_for_user(user, supabase=supabase)
    if not stripe_customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer found for this user yet.")

    _upsert_billing_customer(supabase, user_id=user.id, stripe_customer_id=stripe_customer_id, email=user.email)

    site_url = _resolve_origin_site_url(request)
    return_url = f"{site_url}/dashboard/settings?tab=billing"

    try:
        session = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=stripe_customer_id,
            return_url=return_url,
            idempotency_key=str(uuid4()),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to create billing portal session: {exc}") from exc

    url = getattr(session, "url", None)
    if not url:
        raise HTTPException(status_code=502, detail="Stripe did not return a billing portal URL.")
    return CreatePortalSessionResponse(url=str(url))


class SyncCheckoutSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


class SubscriptionSummary(BaseModel):
    status: Optional[str] = None
    current_period_end: Optional[str] = None
    cancel_at_period_end: Optional[bool] = None
    price_id: Optional[str] = None


class SyncCheckoutSessionResponse(BaseModel):
    synced: bool
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None


@router.post("/sync", response_model=SyncCheckoutSessionResponse)
async def sync_checkout_session(
    payload: SyncCheckoutSessionRequest,
    user: CurrentUser = Depends(get_current_user),
) -> SyncCheckoutSessionResponse:
    _require_stripe_settings()
    supabase = _try_get_supabase()

    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.retrieve,
            payload.session_id,
            expand=["subscription", "customer"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to retrieve Stripe Checkout session: {exc}") from exc

    session_user_id = (
        str(getattr(session, "client_reference_id", "") or "")
        or str(getattr(getattr(session, "metadata", None), "get", lambda *_: None)("user_id") or "")
    )
    if not session_user_id or session_user_id != user.id:
        raise HTTPException(status_code=403, detail="Checkout session does not belong to the current user.")

    customer = getattr(session, "customer", None)
    customer_id = str(getattr(customer, "id", customer) or "") or None

    subscription = getattr(session, "subscription", None)
    subscription_id = str(getattr(subscription, "id", subscription) or "") or None
    subscription_obj: Any | None = subscription

    if subscription_id and (subscription is None or isinstance(subscription, str)):
        try:
            subscription_obj = await asyncio.to_thread(
                stripe.Subscription.retrieve,
                subscription_id,
                expand=["items.data.price"],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Billing: unable to retrieve subscription {subscription_id} while syncing checkout session: {exc}")
            subscription_obj = None

    if customer_id:
        await _ensure_customer_metadata(customer_id=customer_id, user_id=user.id)
        _upsert_billing_customer(supabase, user_id=user.id, stripe_customer_id=customer_id, email=user.email)

    if subscription_obj is not None and hasattr(subscription_obj, "id"):
        _upsert_subscription_from_stripe(supabase, user_id=user.id, subscription=subscription_obj)

    return SyncCheckoutSessionResponse(synced=True, customer_id=customer_id, subscription_id=subscription_id)


class GetSubscriptionResponse(BaseModel):
    is_pro: bool = False
    subscription: Optional[Dict[str, Any]] = None
    customer_id: Optional[str] = None


class CancelSubscriptionResponse(BaseModel):
    canceled: bool = False
    subscription: Optional[Dict[str, Any]] = None


@router.get("/subscription", response_model=GetSubscriptionResponse)
async def get_subscription(
    user: CurrentUser = Depends(get_current_user),
) -> GetSubscriptionResponse:
    _require_stripe_settings()
    supabase = _try_get_supabase()

    stripe_customer_id: Optional[str] = None
    stripe_subscription_record: Optional[Dict[str, Any]] = None

    subscriptions: list[Any] = []
    try:
        subscriptions = await _search_subscriptions_by_user_id(user.id)
    except HTTPException:
        subscriptions = []

    chosen = _pick_best_subscription(subscriptions)

    if chosen is None:
        stripe_customer_id = _get_stripe_customer_id(supabase, user_id=user.id)
        if stripe_customer_id:
            try:
                subscriptions = await _search_subscriptions_by_customer_id(stripe_customer_id)
            except HTTPException:
                subscriptions = []
            chosen = _pick_best_subscription(subscriptions)

    if chosen is None and user.email:
        try:
            customers = await asyncio.to_thread(
                stripe.Customer.list,
                email=user.email,
                limit=10,
            )
        except Exception:  # noqa: BLE001
            customers = None

        customer_data = getattr(customers, "data", None) or []
        email_candidates: list[Any] = []
        for customer in customer_data:
            customer_id = str(getattr(customer, "id", "") or "")
            if not customer_id:
                continue
            try:
                email_candidates.extend(await _search_subscriptions_by_customer_id(customer_id))
            except HTTPException:
                continue

        filtered: list[Any] = []
        for subscription in email_candidates:
            metadata = getattr(subscription, "metadata", None) or {}
            meta_user_id = str(getattr(metadata, "get", lambda *_: None)("user_id") or "")
            if meta_user_id and meta_user_id != user.id:
                continue
            filtered.append(subscription)

        chosen = _pick_best_subscription(filtered or email_candidates)

    if chosen is not None:
        stripe_subscription_record = _stripe_subscription_to_record(chosen, user_id=user.id)
        stripe_customer_id = stripe_subscription_record.get("stripe_customer_id") or stripe_customer_id
        if stripe_customer_id:
            _upsert_billing_customer(supabase, user_id=user.id, stripe_customer_id=stripe_customer_id, email=user.email)
            await _ensure_customer_metadata(customer_id=stripe_customer_id, user_id=user.id)
        _upsert_subscription_from_stripe(supabase, user_id=user.id, subscription=chosen)

    supabase_subscription: Optional[Dict[str, Any]] = None
    if stripe_subscription_record is None and supabase is not None:
        try:
            response = (
                supabase.table("billing_subscriptions")
                .select("*")
                .eq("user_id", user.id)
                .order("current_period_end", desc=True)
                .limit(1)
                .maybe_single()
                .execute()
            )
            if isinstance(response.data, dict):
                supabase_subscription = response.data
        except Exception as exc:  # noqa: BLE001
            print(f"Billing: unable to load subscription for user {user.id}: {exc}")

        stripe_subscription_id = str((supabase_subscription or {}).get("stripe_subscription_id") or "")
        if stripe_subscription_id:
            try:
                stripe_subscription = await asyncio.to_thread(
                    stripe.Subscription.retrieve,
                    stripe_subscription_id,
                    expand=["items.data.price"],
                )
            except Exception:  # noqa: BLE001
                stripe_subscription = None
            if stripe_subscription is not None:
                stripe_subscription_record = _stripe_subscription_to_record(stripe_subscription, user_id=user.id)
                stripe_customer_id = stripe_subscription_record.get("stripe_customer_id") or stripe_customer_id
                if stripe_customer_id:
                    _upsert_billing_customer(
                        supabase,
                        user_id=user.id,
                        stripe_customer_id=stripe_customer_id,
                        email=user.email,
                    )
                    await _ensure_customer_metadata(customer_id=stripe_customer_id, user_id=user.id)
                _upsert_subscription_from_stripe(supabase, user_id=user.id, subscription=stripe_subscription)

    if stripe_subscription_record is not None:
        status = stripe_subscription_record.get("status")
        period_end = _parse_iso_datetime(stripe_subscription_record.get("current_period_end"))
        if status in {"active", "trialing"} and (period_end is None or period_end > datetime.now(timezone.utc)):
            is_pro = True
        elif status == "canceled" and period_end and period_end > datetime.now(timezone.utc):
            is_pro = True
        else:
            is_pro = False
        return GetSubscriptionResponse(is_pro=is_pro, subscription=stripe_subscription_record, customer_id=stripe_customer_id)

    if supabase is None:
        return GetSubscriptionResponse(is_pro=False, subscription=None, customer_id=None)

    if stripe_customer_id is None:
        stripe_customer_id = _get_stripe_customer_id(supabase, user_id=user.id)

    subscription: Optional[Dict[str, Any]] = supabase_subscription
    if subscription is None:
        try:
            response = (
                supabase.table("billing_subscriptions")
                .select("*")
                .eq("user_id", user.id)
                .order("current_period_end", desc=True)
                .limit(1)
                .maybe_single()
                .execute()
            )
            if isinstance(response.data, dict):
                subscription = response.data
        except Exception as exc:  # noqa: BLE001
            print(f"Billing: unable to load subscription for user {user.id}: {exc}")

    status = (subscription or {}).get("status")
    period_end = _parse_iso_datetime((subscription or {}).get("current_period_end"))
    if status in {"active", "trialing"} and (period_end is None or period_end > datetime.now(timezone.utc)):
        is_pro = True
    elif status == "canceled" and period_end and period_end > datetime.now(timezone.utc):
        is_pro = True
    else:
        is_pro = False

    return GetSubscriptionResponse(is_pro=is_pro, subscription=subscription, customer_id=stripe_customer_id)


@router.post("/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(
    user: CurrentUser = Depends(get_current_user),
) -> CancelSubscriptionResponse:
    _require_stripe_settings()
    supabase = _try_get_supabase()

    subscriptions = await _search_subscriptions_by_user_id(user.id)
    chosen = _pick_best_subscription(subscriptions)
    if chosen is None:
        raise HTTPException(status_code=404, detail="No Stripe subscription found for this user.")

    if bool(getattr(chosen, "cancel_at_period_end", False)):
        record = _stripe_subscription_to_record(chosen, user_id=user.id)
        return CancelSubscriptionResponse(canceled=True, subscription=record)

    try:
        updated = await asyncio.to_thread(
            stripe.Subscription.modify,
            str(getattr(chosen, "id", "")),
            cancel_at_period_end=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to cancel Stripe subscription: {exc}") from exc

    _upsert_subscription_from_stripe(supabase, user_id=user.id, subscription=updated)
    record = _stripe_subscription_to_record(updated, user_id=user.id)
    return CancelSubscriptionResponse(canceled=True, subscription=record)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    ensure_env_loaded()
    settings = get_settings()
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or settings.stripe_webhook_secret or "").strip()
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook is not configured (missing STRIPE_WEBHOOK_SECRET).")

    _require_stripe_settings()
    supabase = _try_get_supabase()

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header.")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook payload: {exc}") from exc

    event_type = getattr(event, "type", None) or ""
    data_object = getattr(getattr(event, "data", None), "object", None)

    try:
        if event_type == "checkout.session.completed" and data_object is not None:
            session = data_object
            user_id = str(getattr(session, "client_reference_id", "") or "")
            metadata = getattr(session, "metadata", None)
            if not user_id and metadata:
                user_id = str(getattr(metadata, "get", lambda *_: None)("user_id") or "")

            customer = getattr(session, "customer", None)
            stripe_customer_id = str(getattr(customer, "id", customer) or "")
            if user_id and stripe_customer_id:
                await _ensure_customer_metadata(customer_id=stripe_customer_id, user_id=user_id)
                _upsert_billing_customer(
                    supabase,
                    user_id=user_id,
                    stripe_customer_id=stripe_customer_id,
                    email=None,
                )

        if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"} and data_object is not None:
            subscription = data_object
            metadata = getattr(subscription, "metadata", None)
            user_id = str(getattr(metadata, "get", lambda *_: None)("user_id") or "") if metadata else ""

            customer = getattr(subscription, "customer", None)
            stripe_customer_id = str(getattr(customer, "id", customer) or "")
            if not user_id and stripe_customer_id:
                user_id = _find_user_id_by_customer_id(supabase, stripe_customer_id=stripe_customer_id) or ""

            if user_id:
                if stripe_customer_id:
                    await _ensure_customer_metadata(customer_id=stripe_customer_id, user_id=user_id)
                _upsert_subscription_from_stripe(supabase, user_id=user_id, subscription=subscription)

    except Exception as exc:  # noqa: BLE001
        # Return 500 so Stripe retries if we had a transient error.
        print(f"Billing webhook handler failed for {event_type}: {exc}")
        raise HTTPException(status_code=500, detail="Webhook handler error.") from exc

    return {"status": "success"}
