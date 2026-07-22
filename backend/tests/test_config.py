from app.config import Settings
from pydantic import ValidationError
import pytest


def test_cors_origins_are_read_from_comma_separated_environment_value() -> None:
    settings = Settings(cors_origins="http://localhost:3000, http://localhost:8501")

    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://localhost:8501",
    ]


def test_websocket_origins_are_read_from_separate_exact_allowlist() -> None:
    settings = Settings(
        websocket_allowed_origins=(
            "http://localhost:8501, http://127.0.0.1:8501"
        )
    )

    assert settings.websocket_allowed_origin_list == [
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ]


def test_activity_and_device_threshold_defaults_are_positive() -> None:
    settings = Settings()
    assert settings.activity_inactivity_seconds == 3600
    assert settings.device_offline_seconds == 120
    assert settings.device_offline_scan_seconds == 30


@pytest.mark.parametrize(
    "field",
    ["activity_inactivity_seconds", "device_offline_seconds", "device_offline_scan_seconds"],
)
def test_activity_and_device_thresholds_reject_zero(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})
