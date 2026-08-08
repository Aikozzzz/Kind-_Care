import asyncio
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_dashboard_hub
from app.models.dashboard import (
    DashboardErrorData,
    DashboardErrorMessage,
    DashboardHeartbeatData,
    DashboardHeartbeatMessage,
    DashboardSummaryMessage,
)
from app.models.elderly import ElderlyId
from app.services.dashboard import DashboardStorageUnavailable
from app.services.dashboard_live import DashboardHub, DashboardSubscription
from app.services.elderly import ElderlyProfileNotFound
from app.services.auth import InvalidCredentials, RelationshipDenied, consume_websocket_ticket
from pymongo.asynchronous.database import AsyncDatabase


router = APIRouter(tags=["dashboard-live"])
HubDependency = Annotated[DashboardHub, Depends(get_dashboard_hub)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _message_payload(message: BaseModel) -> dict[str, object]:
    return message.model_dump(mode="json")


def _heartbeat_payload(
    interval_seconds: float,
    poll_interval_seconds: float,
    subscription: DashboardSubscription,
) -> dict[str, object]:
    return _message_payload(
        DashboardHeartbeatMessage(
            data=DashboardHeartbeatData(
                sent_at=datetime.now(UTC),
                interval_seconds=interval_seconds,
                last_summary_check_at=subscription.last_successful_summary_check_at,
                poll_interval_seconds=poll_interval_seconds,
            )
        )
    )


@router.websocket("/ws/dashboard/{elderly_id}")
async def dashboard_websocket(
    websocket: WebSocket,
    elderly_id: ElderlyId,
    hub: HubDependency,
    settings: SettingsDependency,
) -> None:
    if websocket.headers.get("origin") not in settings.websocket_allowed_origin_list:
        await websocket.accept()
        await websocket.close(code=4403, reason="WebSocket origin is not allowed")
        return

    await websocket.accept()
    if settings.websocket_auth_required:
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            if not isinstance(message, dict) or message.get("type") != "authenticate" or not isinstance(message.get("ticket"), str):
                raise InvalidCredentials("WebSocket authentication failed")
            await consume_websocket_ticket(websocket.app.state.database, message["ticket"], elderly_id)
        except (InvalidCredentials, RelationshipDenied, asyncio.TimeoutError, ValueError, RuntimeError):
            await websocket.close(code=4401, reason="WebSocket authentication failed")
            return

    try:
        subscription = await hub.subscribe(elderly_id)
    except ElderlyProfileNotFound:
        await websocket.close(code=4404, reason="Elderly profile not found")
        return
    except DashboardStorageUnavailable:
        await _send_storage_error(websocket)
        return

    await websocket.send_json(
        _message_payload(DashboardSummaryMessage(data=subscription.initial))
    )
    await websocket.send_json(
        _heartbeat_payload(
            settings.websocket_heartbeat_interval,
            settings.websocket_poll_interval,
            subscription,
        )
    )
    next_heartbeat = monotonic() + settings.websocket_heartbeat_interval
    disconnect_task = asyncio.create_task(websocket.receive())
    queue_task = asyncio.create_task(subscription.queue.get())

    try:
        while True:
            timeout = max(0, next_heartbeat - monotonic())
            done, _ = await asyncio.wait(
                {disconnect_task, queue_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                return
            if queue_task in done:
                event = queue_task.result()
                await websocket.send_json(event.message)
                if event.close_code is not None:
                    await websocket.close(code=event.close_code)
                    return
                queue_task = asyncio.create_task(subscription.queue.get())

            now = monotonic()
            if now >= next_heartbeat:
                await websocket.send_json(
                    _heartbeat_payload(
                        settings.websocket_heartbeat_interval,
                        settings.websocket_poll_interval,
                        subscription,
                    )
                )
                next_heartbeat = now + settings.websocket_heartbeat_interval
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    except RuntimeError:
        return
    finally:
        cleanup_task = asyncio.create_task(
            _cleanup_connection(hub, subscription, disconnect_task, queue_task)
        )
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            pass


async def _send_storage_error(websocket: WebSocket) -> None:
    await websocket.send_json(
        _message_payload(
            DashboardErrorMessage(
                data=DashboardErrorData(
                    message="Dashboard data storage is unavailable"
                )
            )
        )
    )
    await websocket.close(code=1011)


async def _cleanup_connection(
    hub: DashboardHub,
    subscription: DashboardSubscription,
    disconnect_task: asyncio.Task[object],
    queue_task: asyncio.Task[object],
) -> None:
    for task in (disconnect_task, queue_task):
        if not task.done():
            task.cancel()
    await hub.unsubscribe(subscription)
    await asyncio.gather(disconnect_task, queue_task, return_exceptions=True)
