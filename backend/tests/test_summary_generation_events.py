from __future__ import annotations

from datetime import datetime, timedelta, timezone, time

import pytest

from app.services.summary_activity import build_activity_buckets, get_summary_generation_metrics


def test_build_activity_buckets_counts_last_8_days() -> None:
    # Arrange: events across the last 10 days; only last 8 should be counted
    today = datetime.now(timezone.utc).date()
    events = []
    for days_ago in range(10):
        dt = datetime.combine(today - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc)
        events.append({"created_at": dt.isoformat()})

    # Act
    buckets = build_activity_buckets(events)

    # Assert
    assert len(buckets) == 8
    # Oldest bucket should be today-7, newest should be today
    assert buckets[0]["date"] == (today - timedelta(days=7)).isoformat()
    assert buckets[-1]["date"] == today.isoformat()
    # Each day in the window has exactly 1 event
    assert all(bucket["count"] == 1 for bucket in buckets)


def test_build_activity_buckets_respects_timezone_offset() -> None:
    # Arrange: one event at 00:30 local time should count toward "today" in that timezone
    tz_offset_minutes = -60  # UTC+1 (e.g., Europe/Copenhagen in winter)
    local_tz = timezone(timedelta(minutes=60))
    local_today = datetime.now(timezone.utc).astimezone(local_tz).date()
    dt_local = datetime.combine(local_today, time(0, 30), tzinfo=local_tz)
    dt_utc = dt_local.astimezone(timezone.utc)

    buckets = build_activity_buckets([{"created_at": dt_utc.isoformat()}], tz_offset_minutes=tz_offset_minutes)

    assert buckets[-1]["date"] == local_today.isoformat()
    assert buckets[-1]["count"] == 1


def test_build_activity_buckets_ignores_out_of_range_events() -> None:
    today = datetime.now(timezone.utc).date()
    events = [
        {"created_at": datetime.combine(today - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc).isoformat()},
        {"created_at": datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat()},
    ]

    buckets = build_activity_buckets(events)

    # Only today's event counts
    assert buckets[-1]["date"] == today.isoformat()
    assert buckets[-1]["count"] == 1
    assert sum(bucket["count"] for bucket in buckets) == 1


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not-a-date",
        123,
        {},
    ],
)
def test_build_activity_from_events_handles_bad_created_at(value) -> None:
    buckets = build_activity_buckets([{"created_at": value}])
    assert len(buckets) == 8
    assert sum(bucket["count"] for bucket in buckets) == 0


class _DummyResponse:
    def __init__(self, *, data, count=None):
        self.data = data
        self.count = count


class _DummyQuery:
    def __init__(self, supabase):
        self._supabase = supabase
        self._count = None
        self._range = None

    def select(self, _columns, *, count=None):
        self._count = count
        return self

    def limit(self, _value):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._supabase.raise_on_execute is not None:
            raise self._supabase.raise_on_execute

        if self._count:
            return _DummyResponse(data=[{"id": "row"}], count=self._supabase.total_count)

        start, end = self._range or (0, len(self._supabase.events) - 1)
        return _DummyResponse(data=self._supabase.events[start : end + 1])


class _DummySupabase:
    def __init__(self, *, events, total_count, raise_on_execute=None):
        self.events = events
        self.total_count = total_count
        self.raise_on_execute = raise_on_execute

    def table(self, _name):
        return _DummyQuery(self)


def test_get_summary_generation_metrics_supabase_falls_back_to_local_cache(monkeypatch) -> None:
    import app.services.summary_activity as summary_activity_module

    today = datetime.now(timezone.utc)
    monkeypatch.setattr(
        summary_activity_module.local_cache,
        "load_summary_events_cache",
        lambda: [{"created_at": today.isoformat(), "filing_id": "00000000-0000-0000-0000-000000000000"}],
    )

    dummy = _DummySupabase(
        events=[],
        total_count=0,
        raise_on_execute=Exception("Could not find the table 'public.filing_summary_events' in the schema cache"),
    )

    total, buckets = get_summary_generation_metrics(supabase_client=dummy)

    assert total == 1
    assert buckets[-1]["count"] == 1


def test_get_summary_generation_metrics_supabase_paginates(monkeypatch) -> None:
    import app.services.summary_activity as summary_activity_module

    monkeypatch.setattr(summary_activity_module.local_cache, "load_summary_events_cache", lambda: [])

    today = datetime.now(timezone.utc)
    events = [{"created_at": today.isoformat()} for _ in range(1001)]
    dummy = _DummySupabase(events=events, total_count=len(events))

    total, buckets = get_summary_generation_metrics(supabase_client=dummy)

    assert total == 1001
    assert buckets[-1]["count"] == 1001


def test_get_summary_generation_metrics_supabase_includes_local_events(monkeypatch) -> None:
    import app.services.summary_activity as summary_activity_module

    today = datetime.now(timezone.utc)
    monkeypatch.setattr(
        summary_activity_module.local_cache,
        "load_summary_events_cache",
        lambda: [{"created_at": today.isoformat(), "filing_id": "00000000-0000-0000-0000-000000000000"}],
    )

    dummy = _DummySupabase(events=[{"created_at": today.isoformat()}], total_count=1)

    total, buckets = get_summary_generation_metrics(supabase_client=dummy)

    assert total == 2
    assert buckets[-1]["count"] == 2
