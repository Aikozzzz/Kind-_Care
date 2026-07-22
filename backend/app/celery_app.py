from amqp.exceptions import MessageNacked
from celery import Celery
from celery.exceptions import CeleryError
from kombu.exceptions import KombuError

from app.config import get_settings
from app.models.health import HealthEvent
from app.models.activity import ActivityEvent
from app.models.device import DeviceEvent


class HealthBrokerUnavailable(Exception):
    """Raised when a health event cannot be published to RabbitMQ."""


class ActivityBrokerUnavailable(Exception):
    """Raised when an activity event cannot be published to RabbitMQ."""


class DeviceBrokerUnavailable(Exception):
    """Raised when a device event cannot be published to RabbitMQ."""


PUBLISH_RETRY_POLICY = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}


settings = get_settings()
celery_app = Celery("kindcare_backend", broker=settings.rabbitmq_url)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_ignore_result=True,
    broker_transport_options={"confirm_publish": True},
    task_publish_retry=True,
    task_publish_retry_policy=PUBLISH_RETRY_POLICY,
)


class CeleryHealthDispatcher:
    def __init__(self, application: Celery = celery_app) -> None:
        self.application = application

    def dispatch(self, event: HealthEvent) -> None:
        try:
            self.application.send_task(
                "workers.health_worker.process_health_data",
                args=[event.model_dump(mode="json")],
                serializer="json",
                retry=True,
                retry_policy=PUBLISH_RETRY_POLICY,
            )
        except (CeleryError, KombuError, MessageNacked, OSError) as error:
            raise HealthBrokerUnavailable() from error


class _CeleryTelemetryDispatcher:
    task_name: str
    error_type: type[Exception]

    def __init__(self, application: Celery = celery_app) -> None:
        self.application = application

    def dispatch(self, event: ActivityEvent | DeviceEvent) -> None:
        try:
            self.application.send_task(
                self.task_name,
                args=[event.model_dump(mode="json")],
                serializer="json",
                retry=True,
                retry_policy=PUBLISH_RETRY_POLICY,
            )
        except (CeleryError, KombuError, MessageNacked, OSError) as error:
            raise self.error_type() from error


class CeleryActivityDispatcher(_CeleryTelemetryDispatcher):
    task_name = "workers.activity_worker.process_activity_data"
    error_type = ActivityBrokerUnavailable


class CeleryDeviceDispatcher(_CeleryTelemetryDispatcher):
    task_name = "workers.device_worker.process_device_heartbeat"
    error_type = DeviceBrokerUnavailable
