import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.celery_app import celery_app
from app.config import Settings, get_settings
from app.dependencies import get_database
from app.models.common import FailureResponse, SuccessResponse


router = APIRouter(tags=["system"])


def check_rabbitmq(timeout: float) -> None:
    connection = celery_app.connection_for_read()
    try:
        connection.ensure_connection(max_retries=0, timeout=timeout)
    finally:
        connection.release()


def get_rabbitmq_checker() -> Callable[[float], None]:
    return check_rabbitmq


@router.get(
    "/health",
    response_model=SuccessResponse[dict[str, str]],
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": FailureResponse[dict[str, str]],
        }
    },
)
async def health_check(
    database: Annotated[AsyncDatabase, Depends(get_database)],
    rabbitmq_checker: Annotated[
        Callable[[float], None], Depends(get_rabbitmq_checker)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SuccessResponse[dict[str, str]] | JSONResponse:
    async def mongodb_status() -> str:
        try:
            await asyncio.wait_for(
                database.command("ping"),
                timeout=settings.readiness_timeout_seconds,
            )
        except (PyMongoError, TimeoutError, OSError):
            return "unavailable"
        return "available"

    async def rabbitmq_status() -> str:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    rabbitmq_checker, settings.readiness_timeout_seconds
                ),
                timeout=settings.readiness_timeout_seconds,
            )
        except Exception:
            return "unavailable"
        return "available"

    mongodb, rabbitmq = await asyncio.gather(
        mongodb_status(),
        rabbitmq_status(),
    )
    data = {
        "status": (
            "healthy"
            if mongodb == rabbitmq == "available"
            else "unhealthy"
        ),
        "mongodb": mongodb,
        "rabbitmq": rabbitmq,
    }
    if data["status"] == "unhealthy":
        failure = FailureResponse(
            message="KindCare API is unhealthy",
            data=data,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(),
        )

    return SuccessResponse(
        message="KindCare API is healthy",
        data=data,
    )
