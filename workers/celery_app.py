import os

from celery import Celery


def _positive_seconds(name: str, default: str) -> float:
    value = float(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


celery_app = Celery(
    "kindcare_worker",
    broker=os.environ.get(
        "RABBITMQ_URL",
        "amqp://kindcare:kindcare_dev_only@localhost:5672//",
    ),
    include=[
        "workers.health_worker",
        "workers.activity_worker",
        "workers.device_worker",
        "workers.reminder_worker",
        "workers.service_health",
    ],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_enable_remote_control=False,
    task_ignore_result=True,
    broker_transport_options={"confirm_publish": True},
    beat_schedule={
        "scan-offline-devices": {
            "task": "workers.device_worker.scan_offline_devices",
            "schedule": _positive_seconds("DEVICE_OFFLINE_SCAN_SECONDS", "30"),
        },
        "scan-inactive-profiles": {
            "task": "workers.activity_worker.scan_inactive_profiles",
            "schedule": _positive_seconds("ACTIVITY_SCAN_SECONDS", "30"),
        },
        "record-service-heartbeat": {
            "task": "workers.service_health.record_service_heartbeat",
            "schedule": _positive_seconds("SERVICE_HEARTBEAT_SECONDS", "15"),
        },
        "scan-missed-reminders": {
            "task": "workers.reminder_worker.scan_missed_reminders",
            "schedule": _positive_seconds("REMINDER_SCAN_SECONDS", "30"),
        },
    },
)
