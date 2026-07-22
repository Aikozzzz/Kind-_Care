import pytest

from mqtt_ingestor.config import Settings


def test_settings_load_required_credentials_and_bounds(monkeypatch) -> None:
    values = {
        "MQTT_BROKER": "broker.local",
        "MQTT_PORT": "1884",
        "MQTT_USERNAME": "node-user",
        "MQTT_PASSWORD": "node-password",
        "MQTT_CLIENT_ID": "bridge-1",
        "API_BASE_URL": "http://api:9000/",
        "MQTT_MAX_PAYLOAD_BYTES": "2048",
        "MQTT_QUEUE_SIZE": "12",
        "MQTT_HTTP_TIMEOUT": "7.5",
        "MQTT_RETRY_INITIAL_SECONDS": "0.25",
        "MQTT_RETRY_MAX_SECONDS": "4",
        "MQTT_RECONNECT_MIN_SECONDS": "2",
        "MQTT_RECONNECT_MAX_SECONDS": "20",
        "MQTT_HEALTH_FILE": "/tmp/bridge-health",
        "MQTT_HEALTH_MAX_AGE_SECONDS": "8",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.broker == "broker.local"
    assert settings.port == 1884
    assert settings.username == "node-user"
    assert settings.password == "node-password"
    assert settings.client_id == "bridge-1"
    assert settings.api_base_url == "http://api:9000"
    assert settings.max_payload_bytes == 2048
    assert settings.queue_size == 12
    assert settings.http_timeout == 7.5
    assert settings.retry_initial_seconds == 0.25
    assert settings.retry_max_seconds == 4
    assert settings.reconnect_min_seconds == 2
    assert settings.reconnect_max_seconds == 20
    assert settings.health_file == "/tmp/bridge-health"
    assert settings.health_max_age_seconds == 8


@pytest.mark.parametrize("name", ["MQTT_USERNAME", "MQTT_PASSWORD"])
def test_settings_require_nonempty_credentials(monkeypatch, name: str) -> None:
    monkeypatch.setenv("MQTT_USERNAME", "kindcare")
    monkeypatch.setenv("MQTT_PASSWORD", "secret")
    monkeypatch.setenv(name, "")

    with pytest.raises(ValueError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MQTT_PORT", "0"),
        ("MQTT_MAX_PAYLOAD_BYTES", "0"),
        ("MQTT_QUEUE_SIZE", "0"),
        ("MQTT_HTTP_TIMEOUT", "0"),
        ("MQTT_RETRY_INITIAL_SECONDS", "0"),
        ("MQTT_RECONNECT_MIN_SECONDS", "0"),
        ("MQTT_HEALTH_MAX_AGE_SECONDS", "0"),
    ],
)
def test_settings_reject_nonpositive_values(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv("MQTT_USERNAME", "kindcare")
    monkeypatch.setenv("MQTT_PASSWORD", "secret")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        Settings.from_env()
