from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.reminder import ReminderCreate, ReminderStatusUpdate


def test_reminder_create_is_strict_and_normalizes_scheduled_time_to_utc() -> None:
    request = ReminderCreate(
        elderly_id="E001",
        medicine_name="Aspirin",
        scheduled_for=datetime(2026, 7, 18, 10, 0, tzinfo=timezone_offset()),
        instructions="After breakfast",
    )

    assert request.scheduled_for == datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ReminderCreate(
            elderly_id="E001",
            medicine_name="Aspirin",
            scheduled_for=datetime(2026, 7, 18, 8, 0),
        )
    with pytest.raises(ValidationError):
        ReminderCreate(
            elderly_id="E001",
            medicine_name="Aspirin",
            scheduled_for=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
            status="taken",
        )


def test_reminder_patch_only_accepts_taken() -> None:
    update = ReminderStatusUpdate(elderly_id="E001", status="taken")
    assert update.elderly_id == "E001"
    assert update.status == "taken"
    with pytest.raises(ValidationError):
        ReminderStatusUpdate(status="taken")
    with pytest.raises(ValidationError):
        ReminderStatusUpdate(elderly_id="E001", status="missed")


def timezone_offset():
    return timezone(timedelta(hours=2))
