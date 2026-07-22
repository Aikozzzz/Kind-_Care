from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "KindCare API"
    mongo_uri: str = "mongodb://localhost:27017/?replicaSet=rs0"
    database_name: str = "kindcare_db"
    rabbitmq_url: str = "amqp://kindcare:kindcare_dev_only@localhost:5672//"
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=4.0)
    cors_origins: str = "http://localhost:8501"
    dashboard_recent_alert_limit: int = Field(default=10, ge=1, le=50)
    dashboard_upcoming_reminder_limit: int = Field(default=10, ge=1, le=50)
    dashboard_recent_reminder_limit: int = Field(default=10, ge=1, le=50)
    websocket_poll_interval: float = Field(default=1.0, gt=0)
    websocket_heartbeat_interval: float = Field(default=15.0, gt=0)
    websocket_allowed_origins: str = (
        "http://localhost:8501,http://127.0.0.1:8501"
    )
    activity_inactivity_seconds: float = Field(default=3600, gt=0)
    device_offline_seconds: float = Field(default=120, gt=0)
    device_offline_scan_seconds: float = Field(default=30, gt=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def websocket_allowed_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.websocket_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
