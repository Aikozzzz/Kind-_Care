import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol

from app.models.dashboard import (
    DashboardErrorData,
    DashboardErrorMessage,
    DashboardSummary,
    DashboardSummaryMessage,
)
from app.services.dashboard import DashboardStorageUnavailable
from app.services.elderly import ElderlyProfileNotFound


logger = logging.getLogger(__name__)


class SummaryProvider(Protocol):
    async def get_summary(self, elderly_id: str) -> DashboardSummary: ...


@dataclass(frozen=True)
class DashboardEvent:
    message: dict[str, object]
    close_code: int | None = None


@dataclass(eq=False)
class _DashboardChannel:
    elderly_id: str
    latest: DashboardSummary
    fingerprint: str
    last_successful_summary_check_at: datetime
    recovering_from_error: bool = False
    subscribers: set[asyncio.Queue[DashboardEvent]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class DashboardSubscription:
    initial: DashboardSummary
    queue: asyncio.Queue[DashboardEvent]
    _channel: _DashboardChannel

    @property
    def poll_task(self) -> asyncio.Task[None]:
        if self._channel.task is None:
            raise RuntimeError("Dashboard poll task is not running")
        return self._channel.task

    @property
    def last_successful_summary_check_at(self) -> datetime:
        return self._channel.last_successful_summary_check_at


class DashboardHub:
    def __init__(
        self,
        service: SummaryProvider,
        poll_interval: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.service = service
        self.poll_interval = poll_interval
        self.clock = clock
        self._channels: dict[str, _DashboardChannel] = {}
        self._lock = asyncio.Lock()

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    def subscriber_count(self, elderly_id: str) -> int:
        channel = self._channels.get(elderly_id)
        return len(channel.subscribers) if channel is not None else 0

    async def subscribe(self, elderly_id: str) -> DashboardSubscription:
        async with self._lock:
            channel = self._channels.get(elderly_id)
            if channel is None:
                summary = await self.service.get_summary(elderly_id)
                channel = _DashboardChannel(
                    elderly_id=elderly_id,
                    latest=summary,
                    fingerprint=summary.model_dump_json(),
                    last_successful_summary_check_at=self.clock(),
                )
                self._channels[elderly_id] = channel
                channel.task = asyncio.create_task(
                    self._poll(channel),
                    name=f"dashboard-poll-{elderly_id}",
                )
            queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=1)
            channel.subscribers.add(queue)
            return DashboardSubscription(channel.latest, queue, channel)

    async def unsubscribe(self, subscription: DashboardSubscription) -> None:
        task: asyncio.Task[None] | None = None
        async with self._lock:
            channel = subscription._channel
            channel.subscribers.discard(subscription.queue)
            if not channel.subscribers:
                if self._channels.get(channel.elderly_id) is channel:
                    self._channels.pop(channel.elderly_id, None)
                task = channel.task
                if task is not None and not task.done():
                    task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
            tasks = [channel.task for channel in channels if channel.task is not None]
            for channel in channels:
                channel.subscribers.clear()
            for task in tasks:
                if not task.done():
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll(self, channel: _DashboardChannel) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                summary = await self.service.get_summary(channel.elderly_id)
            except ElderlyProfileNotFound:
                self._broadcast(
                    channel,
                    DashboardEvent(
                        DashboardErrorMessage(
                            data=DashboardErrorData(message="Elderly profile not found")
                        ).model_dump(mode="json"),
                        close_code=4404,
                    ),
                )
                await self._remove_terminated(channel)
                return
            except DashboardStorageUnavailable:
                channel.recovering_from_error = True
                self._broadcast(
                    channel,
                    DashboardEvent(
                        DashboardErrorMessage(
                            data=DashboardErrorData(
                                message="Dashboard data storage is unavailable"
                            )
                        ).model_dump(mode="json")
                    ),
                )
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Unexpected dashboard poll failure for %s", channel.elderly_id
                )
                channel.recovering_from_error = True
                self._broadcast(
                    channel,
                    DashboardEvent(
                        DashboardErrorMessage(
                            data=DashboardErrorData(
                                message="Dashboard live update failed"
                            )
                        ).model_dump(mode="json")
                    ),
                )
                continue

            channel.last_successful_summary_check_at = self.clock()
            fingerprint = summary.model_dump_json()
            recovering_from_error = channel.recovering_from_error
            channel.recovering_from_error = False
            if fingerprint == channel.fingerprint and not recovering_from_error:
                continue
            channel.latest = summary
            channel.fingerprint = fingerprint
            self._broadcast(
                channel,
                DashboardEvent(
                    DashboardSummaryMessage(data=summary).model_dump(mode="json")
                ),
            )

    def _broadcast(self, channel: _DashboardChannel, event: DashboardEvent) -> None:
        for queue in tuple(channel.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    async def _remove_terminated(self, channel: _DashboardChannel) -> None:
        async with self._lock:
            if self._channels.get(channel.elderly_id) is channel:
                self._channels.pop(channel.elderly_id, None)
