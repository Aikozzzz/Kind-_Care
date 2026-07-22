from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from workers.reminder_worker import missed_boundary, scan_missed_reminders_task


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def test_missed_boundary_is_inclusive_at_grace_threshold() -> None:
    scheduled = NOW - timedelta(seconds=60)
    assert missed_boundary(scheduled, NOW, 60) is True
    assert missed_boundary(scheduled + timedelta(microseconds=1), NOW, 60) is False


def test_scanner_task_uses_server_time_and_configured_bounds(monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_GRACE_SECONDS", "90")
    monkeypatch.setenv("REMINDER_SCAN_BATCH_SIZE", "7")
    monkeypatch.setenv("SCAN_LEASE_SECONDS", "20")
    with patch("workers.reminder_worker.get_database", return_value="database"), patch(
        "workers.reminder_worker.scan_missed_reminders", return_value=3
    ) as scan:
        assert scan_missed_reminders_task() == 3
    args = scan.call_args.args
    assert args[0] == "database"
    assert args[2:] == (90.0, 7, 20)
    assert args[1].tzinfo is UTC
    assert scan.call_args.kwargs["clock"] is not None


@pytest.mark.parametrize(
    "name,value",
    [("REMINDER_GRACE_SECONDS", "0"), ("REMINDER_SCAN_BATCH_SIZE", "0"), ("SCAN_LEASE_SECONDS", "0")],
)
def test_scanner_rejects_non_positive_configuration(monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        scan_missed_reminders_task()


def test_scanner_task_retries_transient_database_failures() -> None:
    task = scan_missed_reminders_task
    assert task.max_retries == 3
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True
