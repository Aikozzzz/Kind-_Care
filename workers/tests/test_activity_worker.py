from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import AutoReconnect

from workers.activity_worker import (
    advance_activity_state,
    activity_event_can_change_anchor,
    activity_payload_hash,
    canonicalize_activity_event,
    find_inactive_anchor,
    process_activity_data,
    scan_inactive_profiles_task,
    scan_inactive_profiles,
)
from workers.celery_app import celery_app


BASE = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def event(number: int, value: str, seconds: int) -> dict[str, object]:
    return {
        "event_id": f"00000000-0000-4000-8000-{number:012d}",
        "elderly_id": "E001",
        "value": value,
        "recorded_at": BASE + timedelta(seconds=seconds),
        "received_at": BASE + timedelta(seconds=seconds),
    }


def test_activity_hash_normalizes_timezone_and_detects_conflict() -> None:
    first = canonicalize_activity_event(event(1, "active", 0))
    equivalent = canonicalize_activity_event(
        {**event(1, "active", 0), "recorded_at": "2026-07-17T10:00:00+02:00"}
    )
    changed = canonicalize_activity_event(event(1, "inactive", 0))
    assert activity_payload_hash(first) == activity_payload_hash(equivalent)
    assert activity_payload_hash(first) != activity_payload_hash(changed)


def test_activity_task_is_live_and_reliable() -> None:
    assert process_activity_data.name in celery_app.tasks
    assert process_activity_data.acks_late is True
    assert process_activity_data.reject_on_worker_lost is True
    assert process_activity_data.max_retries == 3
    assert AutoReconnect in process_activity_data.autoretry_for
    assert scan_inactive_profiles_task.name in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["scan-inactive-profiles"]
    assert schedule["task"] == "workers.activity_worker.scan_inactive_profiles"


def test_activity_state_uses_received_time_and_constant_episode_state() -> None:
    hostile_recorded_at = BASE + timedelta(days=30)
    first = canonicalize_activity_event(
        {**event(1, "inactive", 0), "recorded_at": hostile_recorded_at}
    )
    state, resolve_episode = advance_activity_state(None, first)
    assert state["inactive_since"] == BASE
    assert state["episode_id"].endswith(str(first["event_id"]))
    assert resolve_episode is None

    later = canonicalize_activity_event(event(2, "inactive", 30))
    next_state, resolve_episode = advance_activity_state(state, later)
    assert next_state["inactive_since"] == BASE
    assert next_state["episode_id"] == state["episode_id"]
    assert resolve_episode is None

    active = canonicalize_activity_event(event(3, "active", 40))
    active_state, resolve_episode = advance_activity_state(next_state, active)
    assert active_state["value"] == "active"
    assert active_state["inactive_since"] is None
    assert resolve_episode == state["episode_id"]


def test_activity_state_ignores_events_received_before_latest_state() -> None:
    latest, _ = advance_activity_state(None, canonicalize_activity_event(event(2, "active", 20)))
    stale, resolve_episode = advance_activity_state(
        latest, canonicalize_activity_event(event(1, "inactive", 10))
    )
    assert stale == latest
    assert resolve_episode is None


def test_only_events_inside_current_inactive_episode_can_change_anchor() -> None:
    current, _ = advance_activity_state(
        None,
        canonicalize_activity_event(
            {**event(20, "inactive", 20), "event_id": "id-c"}
        ),
    )
    current["inactive_since"] = BASE + timedelta(seconds=5)

    assert activity_event_can_change_anchor(
        current, canonicalize_activity_event(event(10, "active", 10))
    )
    assert not activity_event_can_change_anchor(
        current, canonicalize_activity_event(event(4, "active", 4))
    )
    assert not activity_event_can_change_anchor(
        current, canonicalize_activity_event(event(21, "active", 21))
    )
    assert activity_event_can_change_anchor(
        current,
        canonicalize_activity_event(
            {**event(10, "active", 20), "event_id": "id-b"}
        ),
    )
    assert not activity_event_can_change_anchor(
        current,
        canonicalize_activity_event(
            {**event(10, "active", 20), "event_id": "id-d"}
        ),
    )
    assert activity_event_can_change_anchor(
        current, canonicalize_activity_event(event(3, "inactive", 3))
    )


def test_inactive_anchor_queries_are_bounded_and_use_history_index() -> None:
    class Cursor:
        def __init__(self, documents: list[dict[str, object]]) -> None:
            self.documents = documents
            self.hint_name = None
            self.position = 0
            self.sort_order = None

        def sort(self, ordering: list[tuple[str, int]]):
            self.sort_order = ordering
            return self

        def hint(self, name: str):
            self.hint_name = name
            return self

        def limit(self, count: int):
            return self

        def __iter__(self):
            return self

        def __next__(self):
            if self.position >= len(self.documents):
                raise StopIteration
            document = self.documents[self.position]
            self.position += 1
            return document

    class ActivityLogs:
        def __init__(self) -> None:
            self.queries: list[dict[str, object]] = []
            self.cursors: list[Cursor] = []

        def find(self, query, projection, session):
            self.queries.append(query)
            documents = (
                [{"received_at": BASE + timedelta(seconds=20), "event_id": "id-b"}]
                if len(self.queries) == 1
                else [{"received_at": BASE + timedelta(seconds=20)}]
            )
            cursor = Cursor(documents)
            self.cursors.append(cursor)
            return cursor

    class Database:
        def __init__(self) -> None:
            self.activity_logs = ActivityLogs()

    database = Database()
    current = {
        "event_id": "id-c",
        "received_at": BASE + timedelta(seconds=20),
        "inactive_since": BASE + timedelta(seconds=5),
    }

    anchor = find_inactive_anchor(database, "E001", current, object())

    assert anchor == BASE + timedelta(seconds=20)
    assert database.activity_logs.queries[0]["$or"] == [
        {"received_at": {"$lt": BASE + timedelta(seconds=20)}},
        {
            "received_at": BASE + timedelta(seconds=20),
            "event_id": {"$lt": "id-c"},
        },
    ]
    assert database.activity_logs.queries[1]["$and"][0]["$or"] == [
        {"received_at": {"$gt": BASE + timedelta(seconds=20)}},
        {
            "received_at": BASE + timedelta(seconds=20),
            "event_id": {"$gt": "id-b"},
        },
    ]
    assert database.activity_logs.queries[1]["$and"][1]["$or"] == [
        {"received_at": {"$lt": BASE + timedelta(seconds=20)}},
        {
            "received_at": BASE + timedelta(seconds=20),
            "event_id": {"$lte": "id-c"},
        },
    ]
    assert [cursor.hint_name for cursor in database.activity_logs.cursors] == [
        "activity_episode_history",
        "activity_episode_history",
    ]
    assert [cursor.sort_order for cursor in database.activity_logs.cursors] == [
        [("received_at", -1), ("event_id", -1)],
        [("received_at", 1), ("event_id", 1)],
    ]


def test_inactivity_scanner_rejects_unbounded_batch_or_lease() -> None:
    with pytest.raises(ValueError):
        scan_inactive_profiles(None, BASE, 60, 0, 30)
    with pytest.raises(ValueError):
        scan_inactive_profiles(None, BASE, 60, 10, 0)
