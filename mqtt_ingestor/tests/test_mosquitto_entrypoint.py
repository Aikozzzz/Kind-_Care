import os
import subprocess
from pathlib import Path

import pytest


ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "mosquitto" / "docker-entrypoint.sh"
)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MQTT_USERNAME", "bad\nuser"),
        ("MQTT_USERNAME", "bad\ruser"),
        ("MQTT_PASSWORD", "bad\npassword"),
        ("MQTT_PASSWORD", "bad\rpassword"),
    ],
)
def test_entrypoint_rejects_credential_line_breaks_before_password_file(
    name: str, value: str
) -> None:
    environment = {
        **os.environ,
        "MQTT_USERNAME": "kindcare",
        "MQTT_PASSWORD": "secret",
        name: value,
    }

    result = subprocess.run(
        ["/bin/sh", str(ENTRYPOINT)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode != 0
    assert f"{name} must not contain CR or LF" in result.stderr
