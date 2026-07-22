import asyncio
from datetime import UTC, date, datetime

import pytest

from app.models.dashboard import DashboardSummary
from app.models.elderly import ElderlyProfile
from app.services.dashboard_live import DashboardHub


def make_summary(risk: str = "normal") -> DashboardSummary:
    now = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
    return DashboardSummary(
        profile=ElderlyProfile(
            elderly_id="E001",
            full_name="Margaret Lee",
            date_of_birth=date(1948, 4, 12),
            active=True,
            created_at=now,
            updated_at=now,
        ),
        latest_health=None,
        current_risk=risk,
        current_alert=None,
        recent_alerts=[],
    )


class ControlledService:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.calls = 0

    async def get_summary(self, elderly_id: str):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_two_subscribers_share_poll_and_receive_same_change() -> None:
    service = ControlledService([make_summary(), make_summary("warning")])
    hub = DashboardHub(service, poll_interval=0.02)

    first = await hub.subscribe("E001")
    second = await hub.subscribe("E001")

    assert service.calls == 1
    assert hub.channel_count == 1
    assert hub.subscriber_count("E001") == 2
    first_event, second_event = await asyncio.gather(
        asyncio.wait_for(first.queue.get(), timeout=0.2),
        asyncio.wait_for(second.queue.get(), timeout=0.2),
    )
    assert first_event.message == second_event.message
    assert first_event.message["type"] == "summary"
    assert first_event.message["data"]["current_risk"] == "warning"
    assert service.calls == 2

    task = first.poll_task
    await hub.unsubscribe(first)
    assert hub.subscriber_count("E001") == 1
    assert not task.done()
    await hub.unsubscribe(second)
    assert hub.channel_count == 0
    assert task.done()


@pytest.mark.asyncio
async def test_hub_close_cancels_every_channel_and_clears_subscribers() -> None:
    service = ControlledService([make_summary()])
    hub = DashboardHub(service, poll_interval=10)
    first = await hub.subscribe("E001")
    second = await hub.subscribe("E002")
    tasks = [first.poll_task, second.poll_task]

    await hub.close()

    assert hub.channel_count == 0
    assert all(task.done() for task in tasks)


@pytest.mark.asyncio
async def test_unexpected_poll_failure_notifies_subscribers_and_allows_recovery() -> None:
    service = ControlledService(
        [make_summary(), RuntimeError("unexpected failure"), make_summary("warning")]
    )
    hub = DashboardHub(service, poll_interval=0.02)
    first = await hub.subscribe("E001")
    second = await hub.subscribe("E001")
    poll_task = first.poll_task
    first_event, second_event = await asyncio.gather(
        asyncio.wait_for(first.queue.get(), timeout=0.2),
        asyncio.wait_for(second.queue.get(), timeout=0.2),
    )
    assert first_event == second_event
    assert first_event.message == {
        "type": "error",
        "data": {"message": "Dashboard live update failed"},
    }
    assert first_event.close_code is None
    assert not poll_task.done()
    recovered_event = await asyncio.wait_for(first.queue.get(), timeout=0.2)
    assert recovered_event.message["type"] == "summary"
    assert recovered_event.message["data"]["current_risk"] == "warning"
    assert hub.channel_count == 1

    await hub.unsubscribe(first)
    await hub.unsubscribe(second)


@pytest.mark.asyncio
async def test_unchanged_summary_is_broadcast_after_recoverable_poll_failure() -> None:
    summary = make_summary()
    service = ControlledService([summary, RuntimeError("unexpected failure"), summary])
    hub = DashboardHub(service, poll_interval=0.02)
    subscription = await hub.subscribe("E001")

    error_event = await asyncio.wait_for(subscription.queue.get(), timeout=0.2)
    assert error_event.message == {
        "type": "error",
        "data": {"message": "Dashboard live update failed"},
    }

    recovered_event = await asyncio.wait_for(subscription.queue.get(), timeout=0.2)
    assert recovered_event.message["type"] == "summary"
    assert recovered_event.message["data"]["current_risk"] == "normal"

    await hub.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_unchanged_successful_poll_advances_shared_summary_check_time() -> None:
    times = iter(
        [
            datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 10, 0, 1, tzinfo=UTC),
        ]
    )
    service = ControlledService([make_summary(), make_summary()])
    hub = DashboardHub(service, poll_interval=0.01, clock=lambda: next(times))

    subscription = await hub.subscribe("E001")
    initial = subscription.last_successful_summary_check_at
    while service.calls < 2:
        await asyncio.sleep(0.005)

    assert subscription.last_successful_summary_check_at > initial
    assert subscription.queue.empty()
    await hub.unsubscribe(subscription)
