from typing import Annotated

from fastapi import Depends
from pymongo.asynchronous.database import AsyncDatabase
from starlette.requests import HTTPConnection

from app.config import Settings, get_settings
from app.services.dashboard import DashboardService
from app.services.dashboard_live import DashboardHub
from app.services.elderly import ElderlyProfileService
from app.services.health import CeleryHealthDispatcher, HealthEventService
from app.services.activity import ActivityEventService, CeleryActivityDispatcher
from app.services.device import CeleryDeviceDispatcher, DeviceEventService
from app.services.alerts import AlertService
from app.services.reminder import ReminderService
from app.services.auth import AuthService


def get_database(connection: HTTPConnection) -> AsyncDatabase:
    return connection.app.state.database


def get_auth_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(database, settings.auth_session_seconds)


def get_elderly_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> ElderlyProfileService:
    return ElderlyProfileService(database.elderly_profiles)


def get_health_dispatcher() -> CeleryHealthDispatcher:
    return CeleryHealthDispatcher()


def get_health_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    dispatcher: Annotated[CeleryHealthDispatcher, Depends(get_health_dispatcher)],
) -> HealthEventService:
    return HealthEventService(
        database.elderly_profiles,
        database.health_idempotency,
        database.health_logs,
        database.alerts,
        dispatcher,
    )


def get_activity_dispatcher() -> CeleryActivityDispatcher:
    return CeleryActivityDispatcher()


def get_activity_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    dispatcher: Annotated[CeleryActivityDispatcher, Depends(get_activity_dispatcher)],
) -> ActivityEventService:
    return ActivityEventService(database.elderly_profiles, database.activity_idempotency, database.activity_logs, dispatcher)


def get_device_dispatcher() -> CeleryDeviceDispatcher:
    return CeleryDeviceDispatcher()


def get_device_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    dispatcher: Annotated[CeleryDeviceDispatcher, Depends(get_device_dispatcher)],
) -> DeviceEventService:
    return DeviceEventService(database.elderly_profiles, database.device_idempotency, database.device_events, dispatcher)


def get_dashboard_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DashboardService:
    return DashboardService(
        database.elderly_profiles,
        database.health_logs,
        database.alerts,
        recent_alert_limit=settings.dashboard_recent_alert_limit,
        activity_logs=database.activity_logs,
        device_status=database.device_status,
        reminders=database.reminders,
        upcoming_reminder_limit=settings.dashboard_upcoming_reminder_limit,
        recent_reminder_limit=settings.dashboard_recent_reminder_limit,
    )


def get_dashboard_hub(
    connection: HTTPConnection,
) -> DashboardHub:
    return connection.app.state.dashboard_hub


def get_reminder_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> ReminderService:
    return ReminderService(
        database.elderly_profiles,
        database.reminder_idempotency,
        database.reminders,
        database.alerts,
    )


def get_alert_service(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> AlertService:
    return AlertService(
        database.alerts,
        database.activity_state,
        database.device_status,
        database.reminders,
    )
