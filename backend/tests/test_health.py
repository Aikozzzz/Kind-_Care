import asyncio
import time

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app.dependencies import get_database
from app.main import app
from app.routes.health import get_rabbitmq_checker, health_check
from app.config import Settings


class HealthyDatabase:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def command(self, command: str) -> None:
        self.commands.append(command)


class UnavailableDatabase:
    async def command(self, command: str) -> None:
        raise ServerSelectionTimeoutError("MongoDB unavailable")


def available_rabbitmq(timeout: float) -> None:
    return None


def unavailable_rabbitmq(timeout: float) -> None:
    raise OSError("RabbitMQ unavailable")


def test_health_endpoint_returns_success_envelope(client: TestClient) -> None:
    database = HealthyDatabase()
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_rabbitmq_checker] = lambda: available_rabbitmq

    response = client.get("/health")

    assert response.status_code == 200
    assert database.commands == ["ping"]
    assert response.json() == {
        "success": True,
        "message": "KindCare API is healthy",
        "data": {
            "status": "healthy",
            "mongodb": "available",
            "rabbitmq": "available",
        },
    }


def test_health_endpoint_returns_service_unavailable_when_mongodb_is_down(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_database] = lambda: UnavailableDatabase()
    app.dependency_overrides[get_rabbitmq_checker] = lambda: available_rabbitmq

    response = client.get("/health")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "success": False,
        "message": "KindCare API is unhealthy",
        "data": {
            "status": "unhealthy",
            "mongodb": "unavailable",
            "rabbitmq": "available",
        },
    }


def test_health_endpoint_returns_service_unavailable_when_rabbitmq_is_down(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_database] = lambda: HealthyDatabase()
    app.dependency_overrides[get_rabbitmq_checker] = lambda: unavailable_rabbitmq

    response = client.get("/health")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["data"] == {
        "status": "unhealthy",
        "mongodb": "available",
        "rabbitmq": "unavailable",
    }


@pytest.mark.asyncio
async def test_blocking_rabbitmq_probe_is_bounded_and_does_not_block_event_loop() -> None:
    ticks: list[bool] = []

    def blocked_rabbitmq(timeout: float) -> None:
        time.sleep(0.2)

    async def ticker() -> None:
        await asyncio.sleep(0.01)
        ticks.append(True)

    settings = Settings(readiness_timeout_seconds=0.02)
    response, _ = await asyncio.gather(
        health_check(HealthyDatabase(), blocked_rabbitmq, settings),
        ticker(),
    )

    assert response.status_code == 503
    assert response.body
    assert ticks == [True]
