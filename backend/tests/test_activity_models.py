from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.activity import ActivityEvent, ActivityEventCreate, ActivityRecord


def test_activity_request_is_strict_and_timestamped() -> None:
    recorded_at = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    event = ActivityEventCreate(
        elderly_id="E001", value="active", recorded_at=recorded_at
    )
    assert event.recorded_at == recorded_at

    with pytest.raises(ValidationError):
        ActivityEventCreate(elderly_id="E001", value="inactive")
    with pytest.raises(ValidationError):
        ActivityEventCreate(
            elderly_id="E001", value="walking", recorded_at=recorded_at
        )
    with pytest.raises(ValidationError):
        ActivityEventCreate(
            elderly_id="E001",
            value="active",
            recorded_at=recorded_at,
            event_id=str(uuid4()),
        )
    with pytest.raises(ValidationError):
        ActivityEventCreate(
            elderly_id="E001",
            value="active",
            recorded_at=recorded_at,
            received_at=recorded_at,
        )


def test_activity_models_serialize_worker_record() -> None:
    now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    event = ActivityEvent(
        event_id=uuid4(), elderly_id="E001", value="inactive", recorded_at=now, received_at=now
    )
    record = ActivityRecord(**event.model_dump(), created_at=now)
    assert record.model_dump(mode="json")["recorded_at"].endswith("Z")
