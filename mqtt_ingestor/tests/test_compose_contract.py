from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_mosquitto_is_pinned_authenticated_persistent_and_local_only() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    dockerfile = (ROOT / "mosquitto" / "Dockerfile").read_text()
    config = (ROOT / "mosquitto" / "mosquitto.conf").read_text()
    entrypoint = (ROOT / "mosquitto" / "docker-entrypoint.sh").read_text()

    assert "mosquitto:" in compose
    assert '"127.0.0.1:1883:1883"' in compose
    assert "mosquitto_data:/mosquitto/data" in compose
    assert "MQTT_USERNAME:" in compose
    assert "MQTT_PASSWORD:" in compose
    assert "mosquitto_sub" in compose
    assert "FROM eclipse-mosquitto:2.0.22" in dockerfile
    assert "allow_anonymous false" in config
    assert "password_file /mosquitto/data/passwords" in config
    assert "persistence true" in config
    assert "persistence_location /mosquitto/data/" in config
    assert "autosave_on_changes true" in config
    assert "autosave_interval 1" in config
    assert "mosquitto_passwd" in entrypoint
    assert "mosquitto_passwd -b" not in entrypoint
    assert "MQTT_USERNAME" in entrypoint
    assert "MQTT_PASSWORD" in entrypoint


def test_ingestor_waits_for_healthy_broker_and_backend() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "mqtt-ingestor:" in compose
    assert "dockerfile: mqtt_ingestor/Dockerfile" in compose
    assert "MQTT_CLIENT_ID: kindcare-mqtt-ingestor" in compose
    assert "python -m mqtt_ingestor.healthcheck" in compose
    assert "mosquitto:\n        condition: service_healthy" in compose
    assert "backend:\n        condition: service_healthy" in compose


def test_dashboard_healthcheck_validates_streamlit_import_context() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "os.chdir('/app/dashboard')" in compose
    assert "import dashboard.api" in compose


def test_environment_documents_local_demo_credentials_and_ingestor_settings() -> None:
    example = (ROOT / ".env.example").read_text()
    backend_example = (ROOT / "backend" / ".env.example").read_text()
    ignored = (ROOT / ".gitignore").read_text()
    docker_ignored = (ROOT / ".dockerignore").read_text()

    assert "MQTT_USERNAME=kindcare_mqtt" in example
    assert "MQTT_PASSWORD=kindcare_mqtt_dev_only" in example
    assert "MQTT_MAX_PAYLOAD_BYTES=16384" in example
    assert "MQTT_RETRY_MAX_SECONDS=30" in example
    assert "MQTT_USERNAME" not in backend_example
    assert "MQTT_PASSWORD" not in backend_example
    assert "passwords" in ignored
    assert ".env" in docker_ignored
