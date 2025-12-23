"""Billing usage helpers for summary quotas."""

from __future__ import annotations

import os
from dataclasses import dataclass
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

import stripe

from app.config import ensure_env_loaded, get_settings
from app.services.summary_activity import count_user_summary_events
from app.models.database import get_supabase_client

PRO_SUMMARY_LIMIT = 100
FREE_SUMMARY_LIMIT = 1


@dataclass(frozen=True)
class SummaryUsageStatus:
    plan: Literal["free", "pro"]
    limit: int
    used: int
    remaining: int
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    subscription_status: Optional[str]
    cancel_at_period_end: Optional[bool]
    is_pro: bool
    billing_unavailable: bool


def _get_stripe_secret_key() -> str:
    ensure_env_loaded()
    settings = get_settings()
    return (os.getenv("STRIPE_SECRET_KEY") or settings.stripe_secret_key or "").strip()


def _configure_stripe() -> bool:
    secret_key = _get_stripe_secret_key()
    if not secret_key:
        return False
    stripe.api_key = secret_key
    stripe.max_network_retries = 2
    return True


def _calendar_month_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _get_subscription_value(subscription: object, field: str) -> Optional[object]:
    if isinstance(subscription, dict):
        return subscription.get(field)
    return getattr(subscription, field, None)


def _parse_datetime(value: object) -> Optional[datetime]:
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
        try:
            if value.endswith("Z"):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return datetime.fromisoformat(value)
        except ValueError:
            return None
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


def _extract_recurring_interval(subscription: object) -> tuple[Optional[str], int]:
    items = _get_subscription_value(subscription, "items")
    data = None
    if isinstance(items, dict):
        data = items.get("data")
    else:
        data = getattr(items, "data", None)
    first = data[0] if data else None
    if first is None:
        return None, 1
    price = first.get("price") if isinstance(first, dict) else getattr(first, "price", None)
    if price is None:
        return None, 1
    recurring = price.get("recurring") if isinstance(price, dict) else getattr(price, "recurring", None)
    if recurring is None:
        return None, 1
    interval = recurring.get("interval") if isinstance(recurring, dict) else getattr(recurring, "interval", None)
    interval_count = recurring.get("interval_count") if isinstance(recurring, dict) else getattr(recurring, "interval_count", None)
    if not interval:
        return None, 1
    try:
        count = int(interval_count or 1)
    except (TypeError, ValueError):
        count = 1
    return str(interval), max(count, 1)


def _subscription_period(subscription: object) -> tuple[Optional[datetime], Optional[datetime]]:
    start_ts = _get_subscription_value(subscription, "current_period_start")
    end_ts = _get_subscription_value(subscription, "current_period_end")
    start = _parse_datetime(start_ts)
    end = _parse_datetime(end_ts)

    if start is None:
        start = _parse_datetime(_get_subscription_value(subscription, "billing_cycle_anchor"))
    if start is None:
        start = _parse_datetime(_get_subscription_value(subscription, "start_date"))
    if start is None:
        start = _parse_datetime(_get_subscription_value(subscription, "created"))

    if end is None and start is not None:
        interval, count = _extract_recurring_interval(subscription)
        if interval:
            end = _add_interval(start, interval=interval, count=count)

    return start, end


def _subscription_canceling(subscription: object, now: datetime) -> bool:
    period_end = _subscription_period(subscription)[1]
    cancel_at_period_end = bool(_get_subscription_value(subscription, "cancel_at_period_end") or False)
    if cancel_at_period_end:
        if period_end and period_end <= now:
            return False
        return True
    cancel_at = _parse_datetime(_get_subscription_value(subscription, "cancel_at"))
    if cancel_at:
        return cancel_at > now
    canceled_at = _parse_datetime(_get_subscription_value(subscription, "canceled_at"))
    if canceled_at and period_end and period_end > now:
        return True
    return False


def _subscription_is_pro(subscription: object, now: datetime) -> bool:
    status = str(_get_subscription_value(subscription, "status") or "")
    period_end = _subscription_period(subscription)[1]
    if period_end and period_end <= now:
        return False
    if status in {"active", "trialing"}:
        return True

    if status == "canceled" and period_end and period_end > now:
        return True

    return False


def _fetch_subscription(user_id: str) -> tuple[Optional[object], Optional[Exception]]:
    if not _configure_stripe():
        return None, None

    search_error: Optional[Exception] = None
    query = f"metadata['user_id']:'{user_id}'"
    try:
        results = stripe.Subscription.search(
            query=query,
            limit=20,
            expand=["data.items.data.price"],
        )
    except Exception as exc:  # noqa: BLE001
        search_error = exc
        results = None

    data = getattr(results, "data", None) or [] if results is not None else []
    if not data:
        customer_id = _get_stripe_customer_id_from_supabase(user_id)
        if not customer_id:
            return None, search_error
        try:
            results = stripe.Subscription.list(
                customer=customer_id,
                status="all",
                limit=20,
                expand=["data.items.data.price"],
            )
        except Exception as exc:  # noqa: BLE001
            return None, search_error or exc
        data = getattr(results, "data", None) or []
        if not data:
            return None, search_error

    active = [sub for sub in data if str(getattr(sub, "status", "") or "") in {"active", "trialing"}]
    candidates = active or data
    return max(candidates, key=lambda sub: int(getattr(sub, "created", 0) or 0)), None


def _fetch_subscription_by_id(subscription_id: Optional[str]) -> tuple[Optional[object], Optional[Exception]]:
    if not subscription_id:
        return None, None
    if not _configure_stripe():
        return None, None
    try:
        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["items.data.price"],
        )
    except Exception as exc:  # noqa: BLE001
        return None, exc
    return subscription, None


def _get_stripe_customer_id_from_supabase(user_id: str) -> Optional[str]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    try:
        supabase = get_supabase_client()
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
    except Exception:
        return None
    return None


def _fetch_subscription_from_supabase(user_id: str) -> Optional[dict]:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("billing_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .order("current_period_end", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
        return response.data if isinstance(response.data, dict) else None
    except Exception:
        return None


def get_summary_usage_status(user_id: str) -> SummaryUsageStatus:
    now = datetime.now(timezone.utc)
    subscription, stripe_error = _fetch_subscription(user_id)
    if subscription is None:
        subscription = _fetch_subscription_from_supabase(user_id)

    if isinstance(subscription, dict):
        stripe_subscription_id = str(subscription.get("stripe_subscription_id") or "")
        stripe_subscription, stripe_id_error = _fetch_subscription_by_id(stripe_subscription_id)
        if stripe_subscription is not None:
            subscription = stripe_subscription
            stripe_error = None
        elif stripe_error is None:
            stripe_error = stripe_id_error

    plan: Literal["free", "pro"] = "free"
    limit = FREE_SUMMARY_LIMIT
    window_start, window_end = _calendar_month_window(now)
    display_start: Optional[datetime] = None
    display_end: Optional[datetime] = None

    subscription_status = None
    cancel_at_period_end = None
    is_pro = False

    billing_unavailable = stripe_error is not None and subscription is None

    if subscription is not None:
        subscription_status = str(_get_subscription_value(subscription, "status") or "") or None
        cancel_at_period_end = _subscription_canceling(subscription, now)
        is_pro = _subscription_is_pro(subscription, now)
        if is_pro:
            plan = "pro"
            limit = PRO_SUMMARY_LIMIT
            sub_start, sub_end = _subscription_period(subscription)
            if not sub_start:
                created_at = _get_subscription_value(subscription, "created") or _get_subscription_value(
                    subscription, "created_at"
                )
                sub_start = _parse_datetime(created_at) if created_at else None
            if sub_start:
                window_start = sub_start
                display_start = sub_start
            if sub_end:
                window_end = sub_end
                display_end = sub_end

    if not is_pro:
        window_start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        window_end = now

    used = count_user_summary_events(user_id=user_id, start=window_start, end=window_end)
    remaining = max(0, limit - used)

    return SummaryUsageStatus(
        plan=plan,
        limit=limit,
        used=used,
        remaining=remaining,
        period_start=display_start,
        period_end=display_end,
        subscription_status=subscription_status,
        cancel_at_period_end=cancel_at_period_end,
        is_pro=is_pro,
        billing_unavailable=billing_unavailable,
    )
