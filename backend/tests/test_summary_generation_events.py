from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.dashboard import _build_activity_from_events


def test_build_activity_from_events_counts_last_8_days() -> None:
    # Arrange: events across the last 10 days; only last 8 should be counted
    today = datetime.now(timezone.utc).date()
    events = []
    for days_ago in range(10):
        dt = datetime.combine(today - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc)
        events.append({"created_at": dt.isoformat()})

    # Act
    buckets = _build_activity_from_events(events)

    # Assert
    assert len(buckets) == 8
    # Oldest bucket should be today-7, newest should be today
    assert buckets[0]["date"] == (today - timedelta(days=7)).isoformat()
    assert buckets[-1]["date"] == today.isoformat()
    # Each day in the window has exactly 1 event
    assert all(bucket["count"] == 1 for bucket in buckets)


def test_build_activity_from_events_ignores_out_of_range_events() -> None:
    today = datetime.now(timezone.utc).date()
    events = [
        {"created_at": datetime.combine(today - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc).isoformat()},
        {"created_at": datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat()},
    ]

    buckets = _build_activity_from_events(events)

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
    buckets = _build_activity_from_events([{"created_at": value}])
    assert len(buckets) == 8
    assert sum(bucket["count"] for bucket in buckets) == 0