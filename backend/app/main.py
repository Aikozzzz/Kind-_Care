from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import database_lifespan
from app.models.common import FailureResponse
from app.routes.alerts import router as alerts_router
from app.routes.dashboard import router as dashboard_router
from app.routes.elderly import router as elderly_router
from app.routes.health import router as health_router
from app.routes.health_data import router as health_data_router
from app.routes.activity import router as activity_router
from app.routes.device_status import router as device_status_router
from app.routes.reminders import router as reminders_router
from app.routes.auth import router as auth_router
from app.routes.relationships import router as relationships_router
from app.routes.telegram import router as telegram_router
from app.routes.telegram_internal import router as telegram_internal_router
from app.routes.access_requests import router as access_requests_router
from app.services.elderly import ElderlyProfileStorageUnavailable
from app.services.dashboard import DashboardStorageUnavailable
from app.services.health import HealthBrokerUnavailable, HealthStorageUnavailable
from app.services.activity import ActivityBrokerUnavailable, ActivityStorageUnavailable
from app.services.device import DeviceBrokerUnavailable, DeviceStorageUnavailable
from app.services.alerts import AlertStorageUnavailable
from app.services.reminder import ReminderStorageUnavailable
from app.services.idempotency import IdempotencyConflict
from app.websocket import router as websocket_router


settings = get_settings()

app = FastAPI(title=settings.app_name, lifespan=database_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(elderly_router)
app.include_router(health_data_router)
app.include_router(alerts_router)
app.include_router(dashboard_router)
app.include_router(websocket_router)
app.include_router(activity_router)
app.include_router(device_status_router)
app.include_router(reminders_router)
app.include_router(auth_router)
app.include_router(relationships_router)
app.include_router(telegram_router)
app.include_router(telegram_internal_router)
app.include_router(access_requests_router)


@app.exception_handler(IdempotencyConflict)
async def idempotency_conflict_handler(
    request: Request, error: IdempotencyConflict
) -> JSONResponse:
    failure = FailureResponse(message=str(error), data={"status": "conflict"})
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=failure.model_dump(),
    )


def _uses_failure_envelope(request: Request) -> bool:
    return any(
        request.url.path == prefix or request.url.path.startswith(f"{prefix}/")
        for prefix in ("/api/activity", "/api/device-status", "/api/reminders", "/api/alerts")
    )


@app.exception_handler(HTTPException)
async def task_four_http_exception_handler(
    request: Request, error: HTTPException
) -> JSONResponse:
    if not _uses_failure_envelope(request):
        return await http_exception_handler(request, error)
    failure = FailureResponse(
        message=str(error.detail), data={"status": "not_found"}
    )
    return JSONResponse(status_code=error.status_code, content=failure.model_dump())


@app.exception_handler(RequestValidationError)
async def task_four_validation_exception_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    if not _uses_failure_envelope(request):
        return await request_validation_exception_handler(request, error)
    failure = FailureResponse(
        message="Request validation failed",
        data={"errors": jsonable_encoder(error.errors())},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=failure.model_dump(),
    )


@app.exception_handler(ElderlyProfileStorageUnavailable)
async def profile_storage_unavailable_handler(
    request: Request,
    error: ElderlyProfileStorageUnavailable,
) -> JSONResponse:
    failure = FailureResponse(
        message="Elderly profile database is unavailable",
        data={"status": "unavailable"},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=failure.model_dump(),
    )


@app.exception_handler(HealthBrokerUnavailable)
async def health_broker_unavailable_handler(
    request: Request,
    error: HealthBrokerUnavailable,
) -> JSONResponse:
    failure = FailureResponse(
        message="Health event broker is unavailable",
        data={"status": "unavailable"},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=failure.model_dump(),
    )


@app.exception_handler(HealthStorageUnavailable)
async def health_storage_unavailable_handler(
    request: Request,
    error: HealthStorageUnavailable,
) -> JSONResponse:
    failure = FailureResponse(
        message="Health data storage is unavailable",
        data={"status": "unavailable"},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=failure.model_dump(),
    )


@app.exception_handler(DashboardStorageUnavailable)
async def dashboard_storage_unavailable_handler(
    request: Request,
    error: DashboardStorageUnavailable,
) -> JSONResponse:
    failure = FailureResponse(
        message="Dashboard data storage is unavailable",
        data={"status": "unavailable"},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=failure.model_dump(),
    )


def _telemetry_unavailable(message: str) -> JSONResponse:
    failure = FailureResponse(message=message, data={"status": "unavailable"})
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=failure.model_dump())


@app.exception_handler(ActivityBrokerUnavailable)
async def activity_broker_unavailable_handler(request: Request, error: ActivityBrokerUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Activity event broker is unavailable")


@app.exception_handler(ActivityStorageUnavailable)
async def activity_storage_unavailable_handler(request: Request, error: ActivityStorageUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Activity data storage is unavailable")


@app.exception_handler(DeviceBrokerUnavailable)
async def device_broker_unavailable_handler(request: Request, error: DeviceBrokerUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Device event broker is unavailable")


@app.exception_handler(DeviceStorageUnavailable)
async def device_storage_unavailable_handler(request: Request, error: DeviceStorageUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Device data storage is unavailable")


@app.exception_handler(ReminderStorageUnavailable)
async def reminder_storage_unavailable_handler(request: Request, error: ReminderStorageUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Reminder storage is unavailable")


@app.exception_handler(AlertStorageUnavailable)
async def alert_storage_unavailable_handler(request: Request, error: AlertStorageUnavailable) -> JSONResponse:
    return _telemetry_unavailable("Alert storage is unavailable")
