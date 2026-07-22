from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import AutoReconnect

from workers.celery_app import celery_app
from workers.device_worker import (
    device_payload_hash,
    canonicalize_device_event,
    heartbeat_is_newer,
    is_offline_boundary,
    process_device_heartbeat,
    scan_offline_devices_task,
    scan_offline_devices,
)


BASE = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def event(event_id: str = "b", recorded_at: object = BASE) -> dict[str, object]:
    return {
        "event_id": event_id,
        "elderly_id": "E001",
        "recorded_at": recorded_at,
        "received_at": BASE,
    }


def test_device_hash_normalizes_timezone_and_detects_conflict() -> None:
    first = canonicalize_device_event(event())
    equivalent = canonicalize_device_event(event(recorded_at="2026-07-17T10:00:00+02:00"))
    changed = canonicalize_device_event(event(recorded_at=BASE + timedelta(seconds=1)))
    assert device_payload_hash(first) == device_payload_hash(equivalent)
    assert device_payload_hash(first) != device_payload_hash(changed)


def test_heartbeat_order_is_timestamp_then_event_id() -> None:
    current = {"last_seen": BASE, "event_id": "b"}
    assert heartbeat_is_newer(BASE + timedelta(seconds=1), "a", current) is True
    assert heartbeat_is_newer(BASE, "c", current) is True
    assert heartbeat_is_newer(BASE, "a", current) is False


def test_device_liveness_uses_server_received_at_not_client_recorded_at() -> None:
    canonical = canonicalize_device_event(
        event(recorded_at=BASE + timedelta(days=30))
    )
    assert canonical["received_at"] == BASE
    assert canonical["recorded_at"] == BASE + timedelta(days=30)


def test_offline_boundary_is_inclusive() -> None:
    now = BASE + timedelta(seconds=120)
    assert is_offline_boundary(BASE + timedelta(microseconds=1), now, 120) is False
    assert is_offline_boundary(BASE, now, 120) is True
    assert is_offline_boundary(BASE - timedelta(seconds=1), now, 120) is True


def test_device_tasks_and_beat_schedule_are_live() -> None:
    assert process_device_heartbeat.name in celery_app.tasks
    assert scan_offline_devices_task.name in celery_app.tasks
    assert process_device_heartbeat.acks_late is True
    assert scan_offline_devices_task.acks_late is True
    assert AutoReconnect in process_device_heartbeat.autoretry_for
    schedule = celery_app.conf.beat_schedule["scan-offline-devices"]
    assert schedule["task"] == "workers.device_worker.scan_offline_devices"
    assert schedule["schedule"] > 0


def test_device_scanner_rejects_unbounded_batch_or_lease() -> None:
    with pytest.raises(ValueError):
        scan_offline_devices(None, BASE, 120, 0, 30)
    with pytest.raises(ValueError):
        scan_offline_devices(None, BASE, 120, 10, 0)
