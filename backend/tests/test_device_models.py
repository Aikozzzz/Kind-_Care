from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.device import DeviceEvent, DeviceEventRecord, DeviceHeartbeatCreate, DeviceStatusRecord


def test_device_heartbeat_request_is_strict_and_timestamped() -> None:
    recorded_at = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    assert DeviceHeartbeatCreate(
        elderly_id="E001", recorded_at=recorded_at
    ).recorded_at == recorded_at

    with pytest.raises(ValidationError):
        DeviceHeartbeatCreate(elderly_id="E001")
    with pytest.raises(ValidationError):
        DeviceHeartbeatCreate(
            elderly_id="E001", recorded_at=recorded_at, status="offline"
        )


def test_device_models_validate_event_and_latest_status() -> None:
    now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    event = DeviceEvent(event_id=uuid4(), elderly_id="E001", recorded_at=now, received_at=now)
    assert DeviceEventRecord(**event.model_dump(), created_at=now).elderly_id == "E001"
    status = DeviceStatusRecord(
        elderly_id="E001",
        event_id=event.event_id,
        status="online",
        last_seen=now,
        updated_at=now,
    )
    assert status.status == "online"


def test_device_record_accepts_internal_mongo_fields() -> None:
    now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    record = DeviceEventRecord.model_validate(
        {
            "_id": "mongo-id",
            "payload_hash": "internal-hash",
            "event_id": uuid4(),
            "elderly_id": "E001",
            "recorded_at": now,
            "received_at": now,
            "created_at": now,
        }
    )
    assert record.elderly_id == "E001"
