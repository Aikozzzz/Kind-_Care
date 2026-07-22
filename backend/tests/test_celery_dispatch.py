from datetime import UTC, datetime

import pytest
from amqp.exceptions import MessageNacked
from kombu.exceptions import OperationalError

from app.celery_app import (
    PUBLISH_RETRY_POLICY,
    CeleryHealthDispatcher,
    HealthBrokerUnavailable,
    celery_app,
)
from app.models.health import HealthEvent


def health_event() -> HealthEvent:
    return HealthEvent(
        elderly_id="E001",
        heart_rate=80,
        temperature=36.7,
        oxygen_level=97,
        movement_status="active",
        medicine_status="taken",
        emergency_pressed=False,
        recorded_at=datetime(2026, 7, 16, 10, 30, tzinfo=UTC),
    )


class RecordingCelery:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def send_task(self, task_name: str, **kwargs: object) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((task_name, kwargs))


def test_publisher_enables_confirms_and_bounded_retry() -> None:
    assert celery_app.conf.broker_transport_options["confirm_publish"] is True
    assert celery_app.conf.task_publish_retry is True
    assert celery_app.conf.task_publish_retry_policy == PUBLISH_RETRY_POLICY
    assert PUBLISH_RETRY_POLICY["max_retries"] == 3


def test_dispatch_requests_json_publish_with_bounded_retry() -> None:
    application = RecordingCelery()

    CeleryHealthDispatcher(application).dispatch(health_event())

    task_name, kwargs = application.calls[0]
    assert task_name == "workers.health_worker.process_health_data"
    assert kwargs["serializer"] == "json"
    assert kwargs["retry"] is True
    assert kwargs["retry_policy"] == PUBLISH_RETRY_POLICY
    payload = kwargs["args"][0]
    assert isinstance(payload["event_id"], str)
    assert payload["recorded_at"].endswith("Z")


def test_dispatch_maps_exhausted_publish_failure_to_broker_unavailable() -> None:
    application = RecordingCelery(OperationalError("broker unavailable"))

    with pytest.raises(HealthBrokerUnavailable):
        CeleryHealthDispatcher(application).dispatch(health_event())


def test_dispatch_maps_negative_publisher_confirm_to_broker_unavailable() -> None:
    application = RecordingCelery(MessageNacked())

    with pytest.raises(HealthBrokerUnavailable):
        CeleryHealthDispatcher(application).dispatch(health_event())
